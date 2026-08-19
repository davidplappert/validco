"""The main endpoint: a person and a time budget become walks worth taking."""

from __future__ import annotations

import logging
import time

from ..datasets.registry import REGISTRY
from ..http.errors import BadRequest, NotFound, Unprocessable
from ..http.request import Request
from ..http.response import Response
from ..models.location import Coordinate, Origin
from ..models.profile import Profile
from ..services.geocoder import Geocoder
from ..services.planner import WalkPlanner
from ..services.scoring import RouteScorer
from ..services.search import Preferences
from .base import Controller
from .system import ATTRIBUTION

LOG = logging.getLogger(__name__)

#: Product limits as much as safety limits — a four-hour walk is not what this
#: app is for, and an unbounded budget would let one request monopolise the
#: function.
MIN_MINUTES, MAX_MINUTES = 5.0, 240.0
MAX_ROUTES = 6

#: How far from the requested point we will look for a walkable path before
#: giving up. Generous enough for rural addresses set back from the road.
MAX_SNAP_M = 600.0


class ProfileFactory:
    """Builds a :class:`Profile` from request JSON.

    Separate from the controller because unit handling and validation messages
    are fiddly enough to deserve their own tests, and because the same
    construction is wanted anywhere a profile arrives over the wire.
    """

    def build(self, request: Request, raw: dict) -> Profile:
        """Validate the health inputs and construct a profile.

        Weight and height are accepted in either unit system: a US user thinks
        in pounds and feet, and the model works in kilograms and centimetres.
        Converting here keeps the physiology code unambiguously metric.
        """
        if not isinstance(raw, dict):
            raise BadRequest("profile must be an object")

        sex = Profile.normalise_sex(raw.get("sex"))
        if sex is None:
            raise BadRequest(
                "profile.sex must be 'male' or 'female'",
                note=(
                    "The gait-speed and body-composition norms this model uses are "
                    "only published split by binary sex; that is a limitation of the "
                    "source literature, not a design choice."
                ),
            )

        age = request.integer(raw.get("age"), "profile.age", 13, 110)

        if raw.get("weight_kg") is not None:
            weight_kg = request.number(raw.get("weight_kg"), "profile.weight_kg", 25, 400)
        elif raw.get("weight_lb") is not None:
            weight_lb = request.number(raw.get("weight_lb"), "profile.weight_lb", 55, 880)
            return Profile.from_imperial(
                sex,
                age,
                weight_lb,
                *self._imperial_height(request, raw),
            )
        else:
            raise BadRequest("profile needs weight_kg or weight_lb")

        height_cm = None
        if raw.get("height_cm") is not None:
            height_cm = request.number(raw.get("height_cm"), "profile.height_cm", 120, 230)
        elif raw.get("height_ft") is not None:
            feet, inches = self._imperial_height(request, raw)
            height_cm = Profile.from_imperial(sex, age, 1.0, feet, inches).height_cm

        return Profile(sex, age, weight_kg, height_cm)

    @staticmethod
    def _imperial_height(request: Request, raw: dict) -> tuple[float | None, float]:
        """Pull feet and inches out of the payload, if present."""
        if raw.get("height_ft") is None:
            return None, 0.0
        return (
            request.number(raw.get("height_ft"), "profile.height_ft", 3, 8),
            request.number(raw.get("height_in"), "profile.height_in", 0, 11.9, 0.0),
        )


class StartPointResolver:
    """Works out where a walk starts and which region that lands in."""

    def __init__(self, registry):
        """Bind the dataset registry to resolve against."""
        self.registry = registry

    def resolve(self, request: Request, body: dict) -> tuple[Coordinate, str, object]:
        """Return ``(coordinate, region_key, geocode_result_or_None)``.

        Accepts either explicit coordinates or a free-text address. With an
        address and no region given, each region is probed in turn — cheap,
        because only the address containers are touched.
        """
        region_key = body.get("region")

        if body.get("lat") is not None and body.get("lon") is not None:
            coordinate = Coordinate(
                request.number(body.get("lat"), "lat", -90, 90),
                request.number(body.get("lon"), "lon", -180, 180),
            )
            key = region_key or self.registry.region_for(coordinate)
            if key is None:
                raise Unprocessable(
                    "those coordinates are outside every covered region",
                    regions=self.registry.keys(),
                )
            return coordinate, key, None

        address = (body.get("address") or "").strip()
        if not address:
            raise BadRequest("provide either address, or lat and lon")

        keys = [region_key] if region_key else self.registry.keys()
        misses: dict[str, object] = {}
        for key in keys:
            result = Geocoder(self.registry.datasets(key).addresses).resolve(address)
            if result.found:
                return result.coordinate, key, result
            misses[key] = result.suggestions or result.reason

        raise NotFound(
            f"could not find {address!r} in any covered region",
            suggestions=misses,
            regions=self.registry.keys(),
        )


class PlanController(Controller):
    """``POST /v1/plan`` — the product."""

    def __init__(self, registry=REGISTRY):
        """Bind the registry and the two collaborators this endpoint needs."""
        super().__init__(registry)
        self.profiles = ProfileFactory()
        self.start_points = StartPointResolver(self.registry)

    def handle(self, request: Request) -> Response:
        """Turn a profile, a start point and a time budget into ranked walks."""
        body = request.json()
        profile = self.profiles.build(request, body.get("profile") or {})
        minutes = request.number(body.get("minutes"), "minutes", MIN_MINUTES, MAX_MINUTES, 30.0)
        max_routes = request.integer(body.get("max_routes"), "max_routes", 1, MAX_ROUTES, 4)
        preferences = Preferences.from_dict(body.get("preferences"))

        coordinate, region_key, geocode = self.start_points.resolve(request, body)
        datasets = self.registry.datasets(region_key)
        graph = datasets.graph

        snapped = graph.nearest_node(coordinate, max_m=MAX_SNAP_M)
        if snapped is None:
            raise Unprocessable(
                f"no walkable street or path within {MAX_SNAP_M:.0f} m of that location",
                lat=coordinate.lat,
                lon=coordinate.lon,
                region=region_key,
            )
        start_node, snap_m = snapped

        planner = WalkPlanner(graph, places=datasets.places, green=datasets.green)
        started = time.perf_counter()
        candidates = planner.plan(profile, start_node, minutes, preferences, max_routes)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        if not candidates:
            raise Unprocessable(
                "could not build a walk from there — the network around that point "
                "is too sparse or disconnected",
                lat=coordinate.lat,
                lon=coordinate.lon,
                region=region_key,
                snap_distance_m=round(snap_m),
            )

        scorer = RouteScorer(green_check=planner.is_green if preferences.prefer_green else None)
        routes = scorer.rank(candidates, minutes, preferences, limit=max_routes)

        origin = Origin(coordinate, graph.coordinate(start_node), snap_m, geocode)
        return Response.ok(
            {
                "region": region_key,
                "origin": origin.to_dict(),
                "profile": profile.to_dict(),
                "request": {"minutes": minutes, "preferences": preferences.to_dict()},
                "routes": [route.to_dict(i, graph.surfaces) for i, route in enumerate(routes)],
                "timing_ms": {"plan": round(elapsed_ms, 1)},
                "attribution": ATTRIBUTION,
            }
        )
