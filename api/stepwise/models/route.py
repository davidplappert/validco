"""A candidate walk, and the pieces it is made of.

:class:`Route` owns everything about one suggestion: the edges walked, the
physiological cost, the surface mix, the geometry to draw, and its own
serialisation. The planner assembles routes; the controller only calls
``to_dict`` on them.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from ..config import FLAG_LABELS, SURFACE_LABELS
from .effort import WalkSegment
from .health import HealthReport
from .suitability import SuitabilityAssessment

LOG = logging.getLogger(__name__)

#: A chart with more than this many points is drawing sub-pixel detail.
MAX_PROFILE_POINTS = 200


class Leg(NamedTuple):
    """One graph edge traversed in one direction.

    ``reverse`` says the edge is walked from its stored ``v`` to its ``u``,
    which matters for both geometry order and the sign of the elevation change.
    """

    edge: int
    reverse: bool


class SurfaceBreakdown:
    """How a route's distance divides across surface types.

    This is the "am I walking on a road or a footpath" question, which is one of
    the two things the product exists to answer, so it gets a model rather than
    a bare dict.
    """

    def __init__(self, metres_by_surface: dict[str, float]):
        """Store the raw metres and precompute the total."""
        self.metres = metres_by_surface
        self.total_m = sum(metres_by_surface.values())

    def percentages(self) -> dict[str, float]:
        """Share of total distance per surface, rounded to one decimal."""
        total = self.total_m or 1.0
        return {k: round(100.0 * v / total, 1) for k, v in sorted(self.metres.items())}

    def share_of(self, surface: str) -> float:
        """Percentage of the route spent on one surface."""
        return self.percentages().get(surface, 0.0)

    def to_dict(self) -> dict[str, float]:
        """Serialise for the API response."""
        return self.percentages()


class ElevationProfile:
    """Cumulative distance against elevation, ready to plot."""

    def __init__(self, points: list[tuple[float, float]]):
        """Take ``(cumulative_metres, elevation_metres)`` pairs in travel order."""
        self.points = points

    def thinned(self, limit: int = MAX_PROFILE_POINTS) -> list[dict[str, float]]:
        """Down-sample to at most ``limit`` points, always keeping the last.

        A 4 km route has hundreds of nodes and the chart is a few hundred pixels
        wide, so most of them land on the same column. Keeping the final point
        explicitly stops the profile ending short of the route's true distance.
        """
        points = self.points
        if len(points) > limit:
            stride = len(points) / float(limit)
            sampled = [points[int(i * stride)] for i in range(limit)]
            sampled.append(points[-1])
            points = sampled
        return [{"m": round(m), "ele": round(ele, 1)} for m, ele in points]

    def to_dict(self) -> list[dict[str, float]]:
        """Serialise for the API response."""
        return self.thinned()


class RouteGeometry:
    """The drawable shape of a route.

    Carries both a single continuous line and a per-surface split. The split is
    what lets the map colour the route by surface, which answers the road-versus-
    path question visually instead of only in a statistic.
    """

    def __init__(self, coordinates: list[list[float]], segments: list[dict[str, Any]]):
        """Store the full polyline and the per-surface runs."""
        self.coordinates = coordinates
        self.segments = segments

    @property
    def is_closed(self) -> bool:
        """Whether the route returns to within a metre or so of its start."""
        if len(self.coordinates) < 2:
            return False
        (lon0, lat0), (lon1, lat1) = self.coordinates[0], self.coordinates[-1]
        return abs(lon0 - lon1) < 1e-5 and abs(lat0 - lat1) < 1e-5

    def to_dict(self) -> dict[str, Any]:
        """Serialise as GeoJSON-shaped output plus the surface runs."""
        return {
            "type": "LineString",
            "coordinates": self.coordinates,
            "segments": self.segments,
        }


class Route:
    """One complete candidate walk.

    Constructed by :class:`~stepwise.services.planner.WalkPlanner`; scored by
    :class:`~stepwise.services.scoring.RouteScorer`; serialised here.
    """

    __slots__ = (
        "legs",
        "nodes",
        "destination",
        "effort",
        "surfaces",
        "streets",
        "geometry",
        "elevation_profile",
        "flags",
        "shape",
        "suitability",
        "health",
        "score",
    )

    def __init__(
        self,
        *,
        legs: list[Leg],
        nodes: list[int],
        effort,
        surfaces: SurfaceBreakdown,
        streets: list[str],
        geometry: RouteGeometry,
        elevation_profile: ElevationProfile,
        flags: int,
        destination: dict[str, Any] | None = None,
        shape: str = "loop",
    ):
        """Assemble a route from its already-computed parts."""
        self.legs = legs
        self.nodes = nodes
        self.effort = effort
        self.surfaces = surfaces
        self.streets = streets
        self.geometry = geometry
        self.elevation_profile = elevation_profile
        self.flags = flags
        self.destination = destination
        self.shape = shape

        self.suitability: SuitabilityAssessment | None = None
        self.health: HealthReport | None = None
        self.score: float = 0.0

    def assess(self, profile) -> None:
        """Attach the health report and suitability assessment for one walker.

        Kept out of ``__init__`` because a route is walker-independent until
        this point, and the planner builds several before deciding which to keep.
        """
        self.health = HealthReport(profile, self.effort)
        self.suitability = SuitabilityAssessment(profile, self.effort)

    @property
    def edge_ids(self) -> set[int]:
        """The set of edges used, for overlap comparison between candidates."""
        return {leg.edge for leg in self.legs}

    def overlap_with(self, other: Route) -> float:
        """Fraction of the smaller route's edges shared with ``other``.

        Used to drop near-duplicate suggestions: three routes down the same
        street are one suggestion, not three.
        """
        mine, theirs = self.edge_ids, other.edge_ids
        if not mine or not theirs:
            return 0.0
        return len(mine & theirs) / min(len(mine), len(theirs))

    @property
    def features(self) -> list[str]:
        """Human-readable flags present anywhere on the route."""
        return [label for bit, label in FLAG_LABELS.items() if self.flags & bit]

    def to_dict(self, index: int, surface_names: list[str]) -> dict[str, Any]:
        """Serialise for the API response.

        ``surface_names`` comes from the dataset so the numeric surface codes in
        the graph and the labels shown to the user cannot drift apart.
        """
        return {
            "id": index,
            "shape": self.shape,
            "score": round(self.score, 1),
            "destination": self.destination,
            "effort": self.effort.to_dict(),
            "health": self.health.to_dict() if self.health else None,
            "suitability": self.suitability.to_dict() if self.suitability else None,
            "surface_breakdown_pct": self.surfaces.to_dict(),
            "surface_labels": {
                surface_names[code]: label for code, label in SURFACE_LABELS.items()
            },
            "features": self.features,
            "streets": self.streets[:25],
            "geometry": self.geometry.to_dict(),
            "elevation_profile": self.elevation_profile.to_dict(),
        }

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return (
            f"Route(shape={self.shape!r}, legs={len(self.legs)}, "
            f"score={self.score:.1f}, {self.effort!r})"
        )


class WalkSegmentBuilder:
    """Turns a leg sequence into the segments the effort model consumes.

    Separated from the planner because it is pure graph-to-physics translation:
    given the walked edges and the nodes between them, produce distances and
    rises. It is also the piece most worth testing directly, since an off-by-one
    here silently corrupts every downstream number.
    """

    def __init__(self, graph):
        """Bind the graph whose edges and elevations will be read."""
        self.graph = graph

    def build(self, legs: list[Leg], nodes: list[int]) -> list[WalkSegment]:
        """Pair each leg with the nodes it leaves and arrives at.

        ``nodes`` has exactly one more entry than ``legs``. The direction bit on
        the leg is not consulted: the ordered node pair already encodes it, and
        the rise follows from their elevations.
        """
        if len(nodes) != len(legs) + 1:
            raise ValueError(
                f"expected {len(legs) + 1} nodes for {len(legs)} legs, got {len(nodes)}"
            )
        graph = self.graph
        return [
            WalkSegment(graph.edge_len[leg.edge], graph.elevation(v) - graph.elevation(u))
            for leg, u, v in zip(legs, nodes[:-1], nodes[1:], strict=True)
        ]
