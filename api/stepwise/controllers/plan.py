"""The main endpoint: a person and a time budget become walks worth taking."""

from __future__ import annotations

import logging
import time

from ..datasets.catalog import BUILDING, FAILED, READY
from ..datasets.registry import REGISTRY
from ..http.errors import (
    AddressNotFound,
    BadRequest,
    ErrorAction,
    RegionBuildFailed,
    RegionBuilding,
    RegionNotCovered,
    Unprocessable,
)
from ..http.request import Request
from ..http.response import Response
from ..models.location import Coordinate, Origin
from ..models.profile import Profile
from ..services.geocoder import AddressParser, Geocoder
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
                self._offer_coverage(coordinate=coordinate)
            return coordinate, key, None

        address = (body.get("address") or "").strip()
        if not address:
            raise BadRequest("provide either address, or lat and lon")

        keys = [region_key] if region_key else self.registry.keys()
        suggestions: list[str] = []
        for key in keys:
            result = Geocoder(self.registry.datasets(key).addresses).resolve(address)
            if result.found:
                return result.coordinate, key, result
            suggestions.extend(result.suggestions)

        # Two very different failures wear the same 404 in most APIs. If we
        # found near-miss street names, the user probably mistyped something in
        # an area we cover; if we found nothing at all, the far likelier
        # explanation is that we do not cover their town — and that has a fix.
        # Deciding between "you mistyped a street we have" and "we don't cover
        # your town" is the single most consequential branch in this endpoint,
        # because only the second has a useful action attached.
        #
        # Fuzzy suggestions alone are not evidence: asked for "10 Downing
        # Street, London", the San Francisco index will happily offer Downey
        # Street. The locality is what actually discriminates.
        if self._locality_is_covered(address) or not self._names_a_locality(address):
            # Only promise suggestions when there actually are some — an empty
            # "did you mean" list under that heading reads as a broken app.
            detail = (
                "We found some similar street names — did you mean one of these?"
                if suggestions
                else (
                    "We have walking data for that area, but no address matching "
                    "what you typed. Check the street name, or try a nearby one."
                )
            )
            raise AddressNotFound(
                f"could not find {address!r} in any covered region",
                detail=detail,
                suggestions=suggestions[:5],
                covered=[r.label for r in self.registry.regions.values()],
            )
        self._offer_coverage(place=address)

    def _names_a_locality(self, address: str) -> bool:
        """Whether the query names a town at all, as opposed to a bare street.

        "1000 California St" names none, so a miss is most likely a typo.
        "1000 California St, Springfield" names one, so if we do not recognise
        it the likeliest explanation is that we have no data for Springfield.
        """
        parser = AddressParser()
        parsed = parser.parse(address)
        # Anything after the first comma, once a state and postcode are removed,
        # is locality context.
        remainder = address.split(",")[1:]
        cleaned = [
            part.strip()
            for part in remainder
            if part.strip() and not part.strip().replace("-", "").isdigit()
        ]
        # A lone trailing state or postcode is not a locality.
        meaningful = [p for p in cleaned if len(p) > 2 and not parsed.postcode == p]
        return bool(meaningful)

    def _locality_is_covered(self, address: str) -> bool:
        """Whether the address names a town we already have data for.

        Without this, a badly mistyped street in San Francisco is reported as
        "we don't cover that area" and offers to download San Francisco — which
        is both wrong and confusing, because we plainly do cover it. Comparing
        the address's locality against the region labels catches that.
        """
        lowered = address.lower()
        for region in self.registry.regions.values():
            # A label reads "San Francisco, CA" or "Peoria & Morton, IL";
            # any of its place names appearing in the query is a strong signal.
            for part in region.label.replace("&", ",").split(","):
                token = part.strip().lower()
                if len(token) > 3 and token in lowered:
                    return True
        return False

    def _offer_coverage(self, place: str | None = None, coordinate=None) -> None:
        """Raise the most useful error we can about somewhere uncovered.

        Always raises. Which error depends on what the catalogue already knows:
        a build in flight becomes "almost there" with progress, a failed one
        explains itself, and an unknown area becomes an offer to build it.
        """
        catalog = self.registry.catalog
        if not catalog.enabled:
            raise RegionNotCovered(
                "location is outside every bundled region",
                detail="This deployment only covers its built-in areas.",
                covered=[r.label for r in self.registry.regions.values()],
            )

        if place:
            from ..datasets.catalog import RegionKey

            key = RegionKey.from_name(place)
            status = catalog.get(key)
            if status is not None and not status.is_stale:
                if status.state == BUILDING:
                    raise RegionBuilding(
                        f"region {key!r} is still building",
                        detail=status.message or RegionBuilding.detail,
                        region=status.to_dict(),
                        action=ErrorAction("poll_region", "Watch progress", key=key),
                    )
                if status.state == FAILED:
                    raise RegionBuildFailed(
                        f"region {key!r} previously failed to build",
                        detail=status.error or RegionBuildFailed.detail,
                        region=status.to_dict(),
                        action=ErrorAction("retry_region", "Try again", key=key),
                    )
                if status.state == READY:
                    # Built, but the address still did not resolve inside it.
                    raise AddressNotFound(
                        f"{place!r} not found within {status.label}",
                        detail=(
                            f"We have walking data for {status.label}, but no address "
                            "matching that. Try including the street name."
                        ),
                    )

        target = place or (f"{coordinate.lat:.4f}, {coordinate.lon:.4f}" if coordinate else "")
        action_params = (
            {"place": place} if place else {"lat": coordinate.lat, "lon": coordinate.lon}
        )
        raise RegionNotCovered(
            f"no coverage for {target!r}",
            action=ErrorAction("add_region", "Add this area", **action_params),
            covered=[r.label for r in self.registry.regions.values()],
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
