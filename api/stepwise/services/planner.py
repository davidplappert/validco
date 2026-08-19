"""Finding walks worth taking.

The product question is not "how do I get from A to B" — it is "I have forty
minutes, where should I walk". That inverts the usual routing problem: the
destination is an *output*, and the route has to come back to where it started.

The method:

1. One **bounded search** from the start, out to half the time budget. That
   single Dijkstra yields every reachable node, its cost, and the path to it.
2. **Anchors** are chosen from that ball — real Overture destinations where
   available, spread across compass bearings so four suggestions are not the
   same suggestion four times.
3. For each anchor, a **second search back to the start with the outbound edges
   penalised**, which produces a genuine loop without ever searching for cycles.
4. Candidates are assembled into :class:`~stepwise.models.route.Route` objects,
   which then score themselves.
"""

from __future__ import annotations

import logging

from ..config import SURFACE_CROSSING
from ..datasets.graph import WalkGraph
from ..models.effort import WalkEffort
from ..models.location import Coordinate
from ..models.profile import Profile
from ..models.route import (
    ElevationProfile,
    Leg,
    Route,
    RouteGeometry,
    SurfaceBreakdown,
    WalkSegmentBuilder,
)
from .search import CROSSING_PAUSE_S, CostModel, GraphSearch, Preferences, SearchResult

LOG = logging.getLogger(__name__)

#: Compass sectors used to spread anchors around the start point.
BEARING_SECTORS = 8

#: Anchors are drawn from nodes between these fractions of the outbound budget.
#: Too close and the loop is trivial; too far and there is no budget to get home.
ANCHOR_BAND = (0.6, 1.0)

#: Bonus applied to a candidate anchor that is a named destination.
NAMED_DESTINATION_BONUS = 0.8

#: How many destinations to snap onto the graph when choosing anchors.
#:
#: Only a handful of anchors are ever used — one per compass sector — so
#: snapping every place in range is wasted work. On a 90-minute San Francisco
#: request that meant roughly 2,000 nearest-node lookups, which profiling put
#: at 25% of total request time. Capping to the best few hundred changes the
#: chosen anchors not at all in practice, because they are ranked by confidence
#: and only the top one per sector survives.
MAX_PLACE_ANCHORS = 160

#: Destinations closer than this fraction of the outbound budget make for
#: trivial loops, so they are not considered as turnaround points.
ANCHOR_INNER_FRACTION = 0.45

#: A candidate sharing more than this fraction of its edges with an already
#: chosen route is a duplicate, not an alternative.
MAX_OVERLAP = 0.6


class Anchor:
    """A turnaround point for a candidate loop."""

    __slots__ = ("node", "place", "sector", "quality")

    def __init__(self, node: int, place: dict | None, sector: int, quality: float):
        """Bind the graph node, any place attached to it, and its ranking."""
        self.node = node
        self.place = place
        self.sector = sector
        self.quality = quality


class AnchorSelector:
    """Chooses where candidate walks should turn around.

    The bearing buckets are the load-bearing part. Without them every anchor
    ends up down the same attractive street, and the app returns one suggestion
    presented four times.
    """

    def __init__(self, graph: WalkGraph, places=None):
        """Bind the graph and, optionally, a destination index."""
        self.graph = graph
        self.places = places

    def select(
        self,
        search: SearchResult,
        origin: Coordinate,
        half_budget_s: float,
        walking_speed_ms: float,
        want: int,
    ) -> list[Anchor]:
        """Pick up to ``want`` turnaround points, spread by compass bearing."""
        candidates = self._candidate_nodes(search, half_budget_s)
        if not candidates:
            return []

        place_by_node = self._places_near(origin, half_budget_s, walking_speed_ms, search)
        by_sector: dict[int, Anchor] = {}

        for node in candidates:
            bearing = origin.bearing_to(self.graph.coordinate(node))
            sector = int(bearing // (360 // BEARING_SECTORS)) % BEARING_SECTORS
            place = place_by_node.get(node)
            # Prefer nodes that use most of the outbound budget and that sit on
            # a named destination.
            quality = search.seconds[node] / half_budget_s
            if place:
                quality += NAMED_DESTINATION_BONUS

            best = by_sector.get(sector)
            if best is None or quality > best.quality:
                by_sector[sector] = Anchor(node, place, sector, quality)

        anchors = sorted(by_sector.values(), key=lambda a: -a.quality)[:want]
        LOG.debug(
            "anchors chosen=%d candidates=%d places=%d",
            len(anchors),
            len(candidates),
            len(place_by_node),
        )
        return anchors

    def _candidate_nodes(self, search: SearchResult, half_budget_s: float) -> list[int]:
        """Nodes far enough out to make a real loop.

        Falls back to a wider band when the network is sparse enough that
        nothing lands in the preferred one — better a short loop than none.
        """
        low, high = ANCHOR_BAND
        nodes = [
            n for n, t in search.seconds.items() if low * half_budget_s <= t <= high * half_budget_s
        ]
        if nodes:
            return nodes
        return [n for n, t in search.seconds.items() if t >= 0.3 * half_budget_s]

    def _places_near(
        self,
        origin: Coordinate,
        half_budget_s: float,
        walking_speed_ms: float,
        search: SearchResult,
    ) -> dict[int, dict]:
        """Map reachable graph nodes to the destinations sitting on them.

        The search radius is a straight-line estimate of how far the outbound
        budget could reach, discounted because streets are not straight.
        """
        if self.places is None:
            return {}

        radius_m = half_budget_s * walking_speed_ms * 0.9
        inner_m = radius_m * ANCHOR_INNER_FRACTION

        # Filter before snapping, not after: the snap is the expensive part.
        candidates = [
            place
            for place in self.places.within(origin, radius_m)
            if place["straight_line_m"] >= inner_m
        ]
        # Rank by Overture's own confidence so the cap keeps the destinations
        # most likely to be real and worth walking to.
        candidates.sort(key=lambda place: -place["confidence"])
        candidates = candidates[:MAX_PLACE_ANCHORS]

        found: dict[int, dict] = {}
        for place in candidates:
            hit = self.graph.nearest_node(Coordinate(place["lat"], place["lon"]), max_m=250.0)
            if hit is None:
                continue
            node = hit[0]
            if search.reached(node) and node not in found:
                found[node] = place
        LOG.debug("place anchors snapped=%d of %d candidates", len(found), len(candidates))
        return found


class RouteBuilder:
    """Assembles a leg sequence into a complete :class:`Route`."""

    def __init__(self, graph: WalkGraph):
        """Bind the graph the legs refer to."""
        self.graph = graph
        self.segments = WalkSegmentBuilder(graph)

    def build(
        self,
        profile: Profile,
        start: int,
        legs: list[tuple[int, bool]],
        destination: dict | None,
        shape: str,
    ) -> Route | None:
        """Turn walked edges into a scored, drawable route.

        Returns ``None`` for an empty leg list rather than raising, because the
        planner routinely generates candidates that turn out to be unroutable
        and that is not an error.
        """
        if not legs:
            return None

        typed_legs = [Leg(edge, reverse) for edge, reverse in legs]
        nodes = self._node_sequence(start, typed_legs)
        walk_segments = self.segments.build(typed_legs, nodes)

        surface_m: dict[str, float] = {}
        streets: list[str] = []
        flags = 0
        pause_s = 0.0
        graph = self.graph

        for leg in typed_legs:
            length = graph.edge_len[leg.edge]
            surface = graph.surface_of(leg.edge)
            surface_m[surface] = surface_m.get(surface, 0.0) + length
            flags |= graph.edge_flags[leg.edge]
            if graph.edge_surface[leg.edge] == SURFACE_CROSSING:
                pause_s += CROSSING_PAUSE_S
            name = graph.edge_name_of(leg.edge)
            # Collapse consecutive runs on the same street into one mention.
            if name and (not streets or streets[-1] != name):
                streets.append(name)

        effort = WalkEffort.evaluate(profile, walk_segments, pause_s=pause_s)
        route = Route(
            legs=typed_legs,
            nodes=nodes,
            effort=effort,
            surfaces=SurfaceBreakdown(surface_m),
            streets=streets,
            geometry=self._geometry(typed_legs),
            elevation_profile=self._elevation_profile(typed_legs, nodes),
            flags=flags,
            destination=destination,
            shape=shape,
        )
        route.assess(profile)
        return route

    def _node_sequence(self, start: int, legs: list[Leg]) -> list[int]:
        """The nodes visited, including the start; one longer than ``legs``."""
        nodes = [start]
        for leg in legs:
            nodes.append(self.graph.head_of(leg.edge, leg.reverse))
        return nodes

    def _geometry(self, legs: list[Leg]) -> RouteGeometry:
        """Build the full polyline plus one run per surface class.

        The per-surface runs are what let the map colour the route by surface,
        turning "62% sidewalk" from a statistic into something visible.
        """
        coordinates: list[list[float]] = []
        runs: list[dict] = []
        current: list[list[float]] = []
        current_surface: str | None = None

        for leg in legs:
            points = self.graph.edge_coords(leg.edge, leg.reverse)
            if not points:
                continue

            # Consecutive edges share a node, so drop the duplicated vertex.
            if coordinates and coordinates[-1] == points[0]:
                coordinates.extend(points[1:])
            else:
                coordinates.extend(points)

            surface = self.graph.surface_of(leg.edge)
            if surface != current_surface:
                if current and current_surface:
                    runs.append({"surface": current_surface, "coordinates": current})
                current = list(points)
                current_surface = surface
            elif current and points and current[-1] == points[0]:
                current.extend(points[1:])
            else:
                current.extend(points)

        if current and current_surface:
            runs.append({"surface": current_surface, "coordinates": current})
        return RouteGeometry(coordinates, runs)

    def _elevation_profile(self, legs: list[Leg], nodes: list[int]) -> ElevationProfile:
        """Cumulative distance against elevation at each node."""
        points: list[tuple[float, float]] = []
        running = 0.0
        for i, node in enumerate(nodes):
            if i > 0:
                running += self.graph.edge_len[legs[i - 1].edge]
            points.append((running, self.graph.elevation(node)))
        return ElevationProfile(points)


class WalkPlanner:
    """Generates candidate walks from a starting point and a time budget."""

    def __init__(self, graph: WalkGraph, places=None, green=None):
        """Bind the graph and the optional destination and green-space indexes."""
        self.graph = graph
        self.places = places
        self.green = green
        self.builder = RouteBuilder(graph)
        self.anchors = AnchorSelector(graph, places)

    def plan(
        self,
        profile: Profile,
        start_node: int,
        target_minutes: float,
        preferences: Preferences,
        max_routes: int = 4,
    ) -> list[Route]:
        """Produce candidate walks of roughly the requested duration.

        Returns them unranked; :class:`~stepwise.services.scoring.RouteScorer`
        decides the order. Splitting generation from ranking keeps the scoring
        policy testable without running a graph search.
        """
        cost_model = CostModel(self.graph, preferences, profile)
        search = GraphSearch(self.graph, cost_model)

        target_s = target_minutes * 60.0
        half_s = target_s * 0.5
        LOG.info(
            "plan start=%d target_min=%.0f prefs=%s",
            start_node,
            target_minutes,
            preferences.to_dict(),
        )

        outbound = search.run(start_node, max_seconds=half_s)
        if len(outbound.cost) < 5:
            LOG.warning(
                "start node %d is nearly isolated (reached %d)", start_node, len(outbound.cost)
            )
            return []

        anchors = self.anchors.select(
            outbound,
            self.graph.coordinate(start_node),
            half_s,
            profile.baseline_speed_ms,
            want=max_routes + 2,
        )

        routes: list[Route] = []
        for anchor in anchors:
            route = self._route_via(profile, search, outbound, start_node, anchor, target_s, half_s)
            if route is not None:
                routes.append(route)

        LOG.info("plan generated=%d candidates from %d anchors", len(routes), len(anchors))
        return routes

    def _route_via(
        self,
        profile: Profile,
        search: GraphSearch,
        outbound: SearchResult,
        start: int,
        anchor: Anchor,
        target_s: float,
        half_s: float,
    ) -> Route | None:
        """Build one candidate that goes out to an anchor and comes back."""
        out_legs = outbound.trace(anchor.node)
        if not out_legs:
            return None

        used = {edge for edge, _ in out_legs}
        spent_s = outbound.seconds.get(anchor.node, half_s)
        # Whatever the outbound leg did not spend, plus 40% slack: a loop home
        # is legitimately longer than the way out, but not unboundedly so.
        return_budget = max(60.0, (target_s - spent_s) * 1.4)

        inbound = search.run(
            anchor.node, max_seconds=return_budget, penalised_edges=used, target=start
        )
        back_legs = inbound.trace(start)

        if back_legs:
            return self.builder.build(profile, start, out_legs + back_legs, anchor.place, "loop")

        # No distinct way home exists — a genuine dead end or a peninsula. An
        # out-and-back is still a perfectly good walk, and saying so is more
        # useful than silently dropping the direction.
        retrace = [(edge, not reverse) for edge, reverse in reversed(out_legs)]
        return self.builder.build(profile, start, out_legs + retrace, anchor.place, "out-and-back")

    def is_green(self, route: Route) -> bool:
        """Whether a route passes near green space.

        Samples a handful of nodes rather than all of them: a route either goes
        near a park or it does not, and eight samples answer that without
        running the proximity check hundreds of times.
        """
        if self.green is None:
            return False
        stride = max(1, len(route.nodes) // 8)
        return any(self.green.near(self.graph.coordinate(node)) for node in route.nodes[::stride])
