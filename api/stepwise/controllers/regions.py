"""Coverage endpoints: what is available, and asking for somewhere new.

This is what makes StepWise universal rather than a two-city demo. Any place in
Overture can be requested; the first person to ask pays for a one-off extraction
and watches a progress bar; everyone after that gets it from cache.

The API deliberately does **not** resolve place names itself. Doing so means
scanning Overture's divisions theme with DuckDB, which belongs in the builder
where that dependency already lives — the request path stays a pure-stdlib
Lambda with a cold start measured in milliseconds.
"""

from __future__ import annotations

import json
import logging
import os

from ..datasets.catalog import (
    BUILDING,
    FAILED,
    PENDING,
    READY,
    RegionKey,
    RegionStatus,
)
from ..http.errors import BadRequest, NotFound, Unprocessable
from ..http.request import Request
from ..http.response import Response
from ..models.location import Coordinate
from .base import Controller
from .system import ATTRIBUTION

LOG = logging.getLogger(__name__)

#: Longest place query we will accept.
MAX_QUERY_CHARS = 120


class RegionsController(Controller):
    """``GET /v1/regions`` — the coverage areas and their dataset sizes."""

    def handle(self, request: Request) -> Response:
        """List every region that can currently serve plans, plus any in flight."""
        ready = [region.to_dict() for region in self.registry.regions.values()]
        ready_keys = {r["key"] for r in ready}

        # Surface in-flight and failed builds too, so a client can show "Austin
        # is 40% built" rather than "Austin does not exist".
        pending = [
            status.to_dict()
            for status in self.registry.catalog.list()
            if status.key not in ready_keys
        ]

        return Response.ok(
            {
                "regions": ready,
                "pending": pending,
                "default": self.registry.default_region_key,
                "on_demand": self.registry.catalog.enabled,
                "attribution": ATTRIBUTION,
            }
        )


class RegionStatusController(Controller):
    """``GET /v1/regions/{key}`` — one region's state and build progress."""

    def handle(self, request: Request) -> Response:
        """Report a region's state, whether bundled, building or unknown."""
        key = request.path_params["key"]

        bundled = self.registry.bundled_regions.get(key)
        if bundled is not None:
            return Response.ok(
                {**bundled.to_dict(), "state": READY, "progress": 1.0, "stage": "ready"}
            )

        status = self.registry.catalog.get(key)
        if status is None:
            raise NotFound(
                f"no region {key!r}",
                hint="POST /v1/regions to add it",
                available=sorted(self.registry.regions),
            )
        return Response.ok(status.to_dict())


class RegionRequestController(Controller):
    """``POST /v1/regions`` — ask for coverage of somewhere new.

    Idempotent by design. Asking twice for the same place returns the same key
    and the same in-flight build, because the catalogue claim is a conditional
    write: two simultaneous requests for Austin produce one extraction.
    """

    def handle(self, request: Request) -> Response:
        """Start, or join, a build for the requested place."""
        if not self.registry.catalog.enabled:
            raise Unprocessable(
                "this deployment serves only its bundled regions",
                regions=sorted(self.registry.regions),
            )

        body = request.json()
        query, label, key = self._identify(request, body)

        # Already usable, or already in flight.
        if key in self.registry.bundled_regions:
            return Response.ok({"key": key, "state": READY, "progress": 1.0, "existing": True})

        existing = self.registry.catalog.get(key)
        if existing is not None and not existing.is_stale:
            LOG.info("region already known key=%s state=%s", key, existing.state)
            return Response(
                200 if existing.state == READY else 202,
                {**existing.to_dict(), "existing": True},
            )

        status = RegionStatus(
            key=key,
            label=label,
            state=PENDING,
            stage="queued",
            message=f"Queued extraction for {label}",
        )

        # A stale build is reclaimed with an unconditional write; a fresh one is
        # claimed conditionally so concurrent callers cannot both start.
        if existing is not None and existing.is_stale:
            LOG.warning("reclaiming stale build key=%s", key)
            self.registry.catalog.put(status)
        elif not self.registry.catalog.claim(status):
            current = self.registry.catalog.get(key)
            return Response(202, {**(current.to_dict() if current else {}), "existing": True})

        self._start_build(key, query, label, body)
        return Response(
            202,
            {
                **status.to_dict(),
                "existing": False,
                "poll": f"/v1/regions/{key}",
                "message": f"Building coverage for {label}. This takes a minute or two.",
            },
        )

    def _identify(self, request: Request, body: dict) -> tuple[dict, str, str]:
        """Work out what was asked for, and the stable key it maps to.

        Accepts either a place name or a coordinate. A name is slugified into
        the key so repeat requests deduplicate; a coordinate is rounded to about
        a kilometre first, so two taps on the same neighbourhood do not build
        the same area twice.
        """
        place = (body.get("place") or "").strip()
        if place:
            if len(place) > MAX_QUERY_CHARS:
                raise BadRequest(f"place must be at most {MAX_QUERY_CHARS} characters")
            return {"place": place}, place, RegionKey.from_name(place)

        if body.get("lat") is not None and body.get("lon") is not None:
            coordinate = Coordinate(
                request.number(body.get("lat"), "lat", -90, 90),
                request.number(body.get("lon"), "lon", -180, 180),
            )
            label = f"{coordinate.lat:.3f}, {coordinate.lon:.3f}"
            key = f"at-{coordinate.lat:.2f}-{coordinate.lon:.2f}".replace(".", "p").replace(
                "-", "m"
            )
            return (
                {"lat": coordinate.lat, "lon": coordinate.lon},
                label,
                RegionKey.from_name(key),
            )

        raise BadRequest("provide either place, or lat and lon")

    def _start_build(self, key: str, query: dict, label: str, body: dict) -> None:
        """Invoke the builder asynchronously.

        ``InvocationType='Event'`` returns immediately, so the caller gets a
        poll URL rather than holding a connection open for the length of an
        extraction. If the invoke itself fails the status is marked failed at
        once, rather than leaving the client polling a build that never started.
        """
        function = os.environ.get("BUILDER_FUNCTION_NAME", "")
        if not function:
            raise Unprocessable("no region builder is configured for this deployment")

        payload = {"key": key, "label": label, **query}
        try:
            import boto3  # noqa: PLC0415 — lazy, so plan requests never import it

            boto3.client("lambda").invoke(
                FunctionName=function,
                InvocationType="Event",
                Payload=json.dumps(payload).encode(),
            )
            LOG.info("builder invoked key=%s label=%s", key, label)
        except Exception as exc:  # noqa: BLE001
            status = self.registry.catalog.get(key)
            if status:
                self.registry.catalog.fail(status, f"could not start the builder: {exc}")
            raise Unprocessable(f"could not start the region build: {exc}") from exc


class RegionDeleteController(Controller):
    """``DELETE /v1/regions/{key}`` — clear a failed build so it can be retried.

    Only failed builds may be removed, and bundled regions never can. This is a
    recovery affordance, not a general delete: a successful region is a shared
    cache entry, and letting any caller evict it would let one user impose a
    rebuild on everyone.
    """

    def handle(self, request: Request) -> Response:
        """Reset a failed region so the next request rebuilds it."""
        key = request.path_params["key"]
        if key in self.registry.bundled_regions:
            raise BadRequest("bundled regions cannot be removed")

        status = self.registry.catalog.get(key)
        if status is None:
            raise NotFound(f"no region {key!r}")
        if status.state not in (FAILED, BUILDING) or (
            status.state == BUILDING and not status.is_stale
        ):
            raise BadRequest(
                "only failed or stalled builds can be cleared",
                state=status.state,
            )

        self.registry.catalog.client.delete_object(
            Bucket=self.registry.catalog.bucket,
            Key=f"{self.registry.catalog.prefix}/{key}/status.json",
        )
        self.registry.invalidate()
        LOG.info("cleared region key=%s previous_state=%s", key, status.state)
        return Response.ok({"key": key, "cleared": True})
