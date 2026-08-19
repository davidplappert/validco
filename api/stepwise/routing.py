"""Route planning: find walks worth taking, not just paths between two points.

The product question is not "how do I get from A to B" — it is "I have 40
minutes, where should I walk". That inverts the usual routing problem: the
destination is an output, not an input, and the route has to come back to where
it started.

The approach:

1. Run one **bounded Dijkstra** from the start, out to roughly half the target
   distance. That single search yields every reachable node, its cost, and the
   path to it — the raw material for every candidate below.
2. Pick **anchors** from that ball: real Overture places (a park, a viewpoint, a
   bakery) where available, plus geometric spread points so the suggestions are
   not all in the same direction.
3. For each anchor, run a **second bounded Dijkstra back to the start with the
   outbound edges penalised**, which turns an out-and-back into a genuine loop
   without ever explicitly searching for cycles.
4. Score every candidate with the health model and the user's stated
   preferences, then return a diverse set.

Cost model
----------
Edge cost is metabolic, not geometric: the base is Minetti's energy cost for the
edge's actual gradient, multiplied by preference weights for surface type. That
means the router avoids a hill because climbing it is genuinely expensive, and
avoids the roadway because the user said they would rather be on a path — two
different kinds of "cost" kept deliberately separate.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass, field

from .config import (
    FLAG_BUSY,
    FLAG_STEPS,
    FLAG_UNPAVED,
    SURFACE_COST,
    SURFACE_CROSSING,
)
from .graph import WalkGraph
from .health import (
    Profile,
    WalkEffort,
    evaluate_walk,
    minetti_cost,
    tobler_speed_factor,
)

LOG = logging.getLogger(__name__)

# Cost of walking a flat metre, used to normalise the gradient term so a flat
# route's cost equals its length and the numbers stay interpretable.
FLAT_COST = minetti_cost(0.0)

# Seconds lost at a signalised crossing, averaged over arrival times.
CROSSING_PAUSE_S = 12.0

# How much more expensive a re-used edge is on the return leg. High enough to
# push the route onto a different street, low enough that a dead-end peninsula
# can still be walked back out of.
REUSE_PENALTY = 4.0


@dataclass
class Preferences:
    """What the walker wants, beyond distance."""

    prefer_paths: bool = True
    avoid_hills: bool = False
    avoid_stairs: bool = False
    avoid_busy_roads: bool = True
    prefer_green: bool = False

    # Derived multiplier on the gradient term. >1 makes hills disproportionately
    # expensive; the router will take a longer flat detour to avoid them.
    @property
    def hill_exponent(self) -> float:
        return 2.2 if self.avoid_hills else 1.0

    @classmethod
    def from_dict(cls, raw: dict | None) -> Preferences:
        raw = raw or {}
        return cls(
            prefer_paths=bool(raw.get("prefer_paths", True)),
            avoid_hills=bool(raw.get("avoid_hills", False)),
            avoid_stairs=bool(raw.get("avoid_stairs", False)),
            avoid_busy_roads=bool(raw.get("avoid_busy_roads", True)),
            prefer_green=bool(raw.get("prefer_green", False)),
        )

    def to_dict(self) -> dict:
        return {
            "prefer_paths": self.prefer_paths,
            "avoid_hills": self.avoid_hills,
            "avoid_stairs": self.avoid_stairs,
            "avoid_busy_roads": self.avoid_busy_roads,
            "prefer_green": self.prefer_green,
        }


class CostModel:
    """Turns an edge traversal into a routing cost and a predicted duration.

    Both fall out of the same gradient computation, so they are produced
    together: the inner loop of Dijkstra runs millions of times and computing
    the grade twice per edge was measurable.
    """

    def __init__(self, graph: WalkGraph, prefs: Preferences, profile: Profile):
        self.g = graph
        self.p = prefs
        self.v_flat = profile.baseline_speed_ms
        # Precompute per-surface multipliers once rather than branching per edge.
        self.surface_mult = dict(SURFACE_COST)
        if not prefs.prefer_paths:
            # Neutral on surface: the walker just wants the efficient route.
            self.surface_mult = {k: 1.0 + (v - 1.0) * 0.25 for k, v in SURFACE_COST.items()}
        self.hill_exp = prefs.hill_exponent

    def edge_metrics(self, edge: int, reverse: bool) -> tuple[float, float]:
        """``(routing_cost, seconds)`` for traversing one edge in one direction."""
        g = self.g
        length = g.edge_len[edge]
        u, v = g.edge_u[edge], g.edge_v[edge]
        if reverse:
            u, v = v, u
        rise = g.elevation(v) - g.elevation(u)
        grade = rise / length if length > 0 else 0.0

        # Gradient term, normalised so flat == 1.0.
        gradient_mult = (minetti_cost(grade) / FLAT_COST) ** self.hill_exp
        cost = length * gradient_mult * self.surface_mult[g.edge_surface[edge]]

        # Duration uses the same physiology as the final scoring, so the budget
        # the search enforces and the time the user is shown cannot disagree.
        speed = max(0.25, self.v_flat * tobler_speed_factor(grade))
        seconds = length / speed

        flags = g.edge_flags[edge]
        if flags & FLAG_STEPS:
            # Stairs are a hard no when asked for; otherwise merely unpleasant.
            if self.p.avoid_stairs:
                return math.inf, math.inf
            cost *= 1.5
        if self.p.avoid_busy_roads and (flags & FLAG_BUSY):
            cost *= 1.4
        if flags & FLAG_UNPAVED:
            cost *= 1.15 if self.p.prefer_paths else 1.05
        if g.edge_surface[edge] == SURFACE_CROSSING:
            # Waiting at the light is part of how long the walk takes.
            seconds += CROSSING_PAUSE_S
        return cost, seconds


@dataclass
class SearchResult:
    """Output of one bounded Dijkstra."""

    dist: dict[int, float]  # node -> accumulated routing cost
    metres: dict[int, float]  # node -> accumulated real distance
    seconds: dict[int, float]  # node -> accumulated predicted walking time
    prev: dict[int, tuple[int, int, int]]  # node -> (from_node, edge, reverse)
    source: int


def dijkstra(
    graph: WalkGraph,
    source: int,
    cost: CostModel,
    max_seconds: float,
    penalty_edges: set[int] | None = None,
    target: int | None = None,
) -> SearchResult:
    """Bounded shortest-path search from one node.

    The bound is *predicted walking time*, not distance and not cost. Distance
    would let a 40-minute request return an hour of climbing, because a
    kilometre uphill is not a kilometre. A cost bound would be worse still,
    since cost carries preference weights that have nothing to do with the
    clock. Time is the budget the user actually stated.

    ``penalty_edges`` multiplies the cost of already-used edges, which is how the
    return leg is pushed onto different streets to form a loop.
    """
    dist = {source: 0.0}
    metres = {source: 0.0}
    seconds = {source: 0.0}
    prev: dict[int, tuple[int, int, int]] = {}
    heap: list[tuple[float, int]] = [(0.0, source)]
    settled: set[int] = set()
    adj_start, adj_edge = graph.adj_start, graph.adj_edge
    edge_u, edge_v, edge_len = graph.edge_u, graph.edge_v, graph.edge_len
    pops = 0

    while heap:
        d, node = heapq.heappop(heap)
        if node in settled:
            continue
        settled.add(node)
        pops += 1
        if target is not None and node == target:
            break
        base_m = metres[node]
        base_s = seconds[node]
        if base_s > max_seconds:
            continue

        for k in range(adj_start[node], adj_start[node + 1]):
            packed = adj_edge[k]
            edge = packed >> 1
            reverse = packed & 1
            nxt = edge_u[edge] if reverse else edge_v[edge]
            if nxt in settled:
                continue
            c, step_s = cost.edge_metrics(edge, bool(reverse))
            if c == math.inf or base_s + step_s > max_seconds:
                continue
            if penalty_edges and edge in penalty_edges:
                c *= REUSE_PENALTY
            nd = d + c
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                metres[nxt] = base_m + edge_len[edge]
                seconds[nxt] = base_s + step_s
                prev[nxt] = (node, edge, reverse)
                heapq.heappush(heap, (nd, nxt))

    LOG.debug(
        "dijkstra source=%d settled=%d reached=%d budget_s=%.0f",
        source,
        pops,
        len(dist),
        max_seconds,
    )
    return SearchResult(dist=dist, metres=metres, seconds=seconds, prev=prev, source=source)


def trace(result: SearchResult, target: int) -> list[tuple[int, int]]:
    """Walk the predecessor chain back to the source; returns (edge, reverse) in travel order."""
    out: list[tuple[int, int]] = []
    node = target
    guard = 0
    while node != result.source:
        step = result.prev.get(node)
        if step is None:
            return []
        from_node, edge, reverse = step
        out.append((edge, reverse))
        node = from_node
        guard += 1
        if guard > 100_000:  # pathological safety net; should never trigger
            LOG.error("trace exceeded guard from target=%d", target)
            return []
    out.reverse()
    return out


@dataclass
class Route:
    """A complete candidate walk."""

    legs: list[tuple[int, int]]  # (edge, reverse) in travel order
    nodes: list[int]
    anchor: dict | None
    effort: WalkEffort
    surface_breakdown: dict[str, float]
    streets: list[str]
    suitability: dict = field(default_factory=dict)
    score: float = 0.0
    shape: str = "loop"


class Planner:
    """Generates and ranks candidate walks from a starting point."""

    def __init__(self, graph: WalkGraph, places=None, green=None):
        self.g = graph
        self.places = places
        self.green = green

    # --- assembling one route --------------------------------------------

    def _node_sequence(self, start: int, legs: list[tuple[int, int]]) -> list[int]:
        nodes = [start]
        for edge, reverse in legs:
            nodes.append(self.g.edge_u[edge] if reverse else self.g.edge_v[edge])
        return nodes

    def build_route(
        self,
        profile: Profile,
        start: int,
        legs: list[tuple[int, int]],
        anchor: dict | None,
        shape: str,
    ) -> Route | None:
        if not legs:
            return None
        g = self.g
        nodes = self._node_sequence(start, legs)

        steps: list[tuple[float, float]] = []
        surface_m: dict[str, float] = {}
        streets: list[str] = []
        pause = 0.0
        # nodes has one more entry than legs — pair each leg with the node it
        # leaves and the node it arrives at. The direction bit is not needed
        # here: (u, v) already carries it, and the rise is derived from them.
        for (edge, _reverse), u, v in zip(legs, nodes[:-1], nodes[1:], strict=True):
            length = g.edge_len[edge]
            rise = g.elevation(v) - g.elevation(u)
            steps.append((length, rise))
            surface = g.surface_of(edge)
            surface_m[surface] = surface_m.get(surface, 0.0) + length
            if g.edge_surface[edge] == SURFACE_CROSSING:
                pause += CROSSING_PAUSE_S
            name = g.edge_name_of(edge)
            if name and (not streets or streets[-1] != name):
                streets.append(name)

        effort = evaluate_walk(profile, steps, pause_s=pause)
        total = sum(surface_m.values()) or 1.0
        breakdown = {k: round(100.0 * v / total, 1) for k, v in sorted(surface_m.items())}
        return Route(
            legs=legs,
            nodes=nodes,
            anchor=anchor,
            effort=effort,
            surface_breakdown=breakdown,
            streets=streets,
            shape=shape,
        )

    # --- candidate generation --------------------------------------------

    def _anchors(
        self, out: SearchResult, start_lat: float, start_lon: float, half_s: float, want: int
    ) -> list[dict]:
        """Choose turnaround points: real destinations first, then spread.

        The bearing buckets are the important part. Without them every anchor
        ends up down the same attractive street and the "three suggestions" are
        one suggestion three times.
        """
        g = self.g
        reachable = [n for n, t in out.seconds.items() if 0.6 * half_s <= t <= half_s]
        if not reachable:
            reachable = [n for n, t in out.seconds.items() if t >= 0.3 * half_s]
        if not reachable:
            return []

        by_bearing: dict[int, list[tuple[float, int, dict | None]]] = {}

        # Named Overture places make far better anchors than arbitrary junctions:
        # "a loop out to Alta Plaza Park" is a walk someone wants to take.
        place_nodes: dict[int, dict] = {}
        if self.places is not None:
            # Straight-line radius that a half-budget of walking could plausibly
            # cover, allowing for the fact that streets are not straight.
            radius = half_s * self.v_flat * 0.9
            for place in self.places.within(start_lat, start_lon, radius):
                hit = g.nearest_node(place["lat"], place["lon"], max_m=250.0)
                if hit is None:
                    continue
                node = hit[0]
                if node in out.metres and node not in place_nodes:
                    place_nodes[node] = place

        for node in reachable:
            bearing = _bearing(start_lat, start_lon, g.node_lat[node], g.node_lon[node])
            bucket = int(bearing // 45) % 8
            place = place_nodes.get(node)
            # Prefer nodes that are both far out (close to the half-distance
            # budget) and attached to a real destination.
            reach = out.seconds[node]
            quality = reach / half_s + (0.8 if place else 0.0)
            by_bearing.setdefault(bucket, []).append((quality, node, place))

        anchors: list[dict] = []
        buckets = sorted(by_bearing, key=lambda b: -max(q for q, _, _ in by_bearing[b]))
        for bucket in buckets:
            best = max(by_bearing[bucket], key=lambda t: t[0])
            _, node, place = best
            anchors.append({"node": node, "place": place, "bearing_bucket": bucket})
            if len(anchors) >= want:
                break
        LOG.debug(
            "anchors chosen=%d from reachable=%d places=%d",
            len(anchors),
            len(reachable),
            len(place_nodes),
        )
        return anchors

    def plan(
        self,
        profile: Profile,
        start: int,
        target_minutes: float,
        prefs: Preferences,
        max_routes: int = 4,
    ) -> list[Route]:
        """Produce a ranked, diverse set of walks of roughly the requested length."""
        g = self.g
        cost = CostModel(g, prefs, profile)
        self.v_flat = profile.baseline_speed_ms

        # The whole budget is expressed in seconds, so hills and crossings are
        # charged against the walk before a candidate is ever built.
        target_s = target_minutes * 60.0
        half_s = target_s * 0.5
        LOG.info(
            "plan start_node=%d target_min=%.0f target_s=%.0f half_s=%.0f prefs=%s",
            start,
            target_minutes,
            target_s,
            half_s,
            prefs.to_dict(),
        )

        outbound = dijkstra(g, start, cost, max_seconds=half_s)
        if len(outbound.dist) < 5:
            LOG.warning("start node %d is nearly isolated (reached %d)", start, len(outbound.dist))
            return []

        anchors = self._anchors(
            outbound, g.node_lat[start], g.node_lon[start], half_s, want=max_routes + 2
        )

        routes: list[Route] = []
        for anchor in anchors:
            node = anchor["node"]
            out_legs = trace(outbound, node)
            if not out_legs:
                continue
            used = {e for e, _ in out_legs}

            # Return leg: same cost model, but re-using the outbound streets is
            # expensive, so the search naturally finds a different way home.
            # Its budget is whatever the outbound leg did not spend, plus 40%
            # slack — a loop home is legitimately longer than the way out, but
            # not unboundedly so.
            spent_s = outbound.seconds.get(node, half_s)
            back = dijkstra(
                g,
                node,
                cost,
                max_seconds=max(60.0, (target_s - spent_s) * 1.4),
                penalty_edges=used,
                target=start,
            )
            back_legs = trace(back, start)

            if back_legs:
                route = self.build_route(
                    profile, start, out_legs + back_legs, anchor["place"], "loop"
                )
            else:
                # No distinct return exists (a genuine dead end, or a peninsula).
                # An out-and-back is still a perfectly good walk — say so.
                retrace = [(e, 1 - r) for e, r in reversed(out_legs)]
                route = self.build_route(
                    profile, start, out_legs + retrace, anchor["place"], "out-and-back"
                )
            if route is not None:
                routes.append(route)

        LOG.info("plan generated=%d candidate routes", len(routes))
        return self._rank(routes, profile, target_minutes, prefs, max_routes)

    # --- ranking -----------------------------------------------------------

    def _rank(
        self,
        routes: list[Route],
        profile: Profile,
        target_minutes: float,
        prefs: Preferences,
        max_routes: int,
    ) -> list[Route]:
        from .health import suitability

        for route in routes:
            route.suitability = suitability(profile, route.effort)
            minutes = route.effort.duration_s / 60.0

            # Closeness to the requested duration is the dominant term — a
            # beautiful 70-minute route is the wrong answer to "I have 30
            # minutes".
            err = abs(minutes - target_minutes) / max(1.0, target_minutes)
            score = 100.0 * math.exp(-3.0 * err)

            # Then how well it suits this walker.
            score = 0.6 * score + 0.4 * route.suitability["score"]

            # Then preference bonuses.
            path_pct = route.surface_breakdown.get("path", 0.0)
            if prefs.prefer_paths:
                score += path_pct * 0.12
            if prefs.prefer_green and self.green is not None:
                score += 8.0 if self._is_green(route) else 0.0
            if route.anchor is not None:
                score += 6.0  # a named destination makes for a better suggestion
            if route.shape == "loop":
                score += 5.0  # loops beat retracing your steps

            route.score = round(score, 1)

        routes.sort(key=lambda r: -r.score)
        deduped = self._diversify(routes, max_routes)
        LOG.info(
            "ranked routes=%d returned=%d top_score=%.1f",
            len(routes),
            len(deduped),
            deduped[0].score if deduped else 0.0,
        )
        return deduped

    def _is_green(self, route: Route) -> bool:
        g = self.g
        # Sample a handful of nodes rather than all of them; a route either goes
        # near a park or it does not, and 8 samples answers that.
        step = max(1, len(route.nodes) // 8)
        for node in route.nodes[::step]:
            if self.green.near(g.node_lat[node], g.node_lon[node], extra_m=120.0):
                return True
        return False

    def _diversify(self, routes: list[Route], limit: int) -> list[Route]:
        """Drop candidates that overlap heavily with an already-chosen route."""
        chosen: list[Route] = []
        for route in routes:
            edges = {e for e, _ in route.legs}
            if any(_overlap(edges, {e for e, _ in c.legs}) > 0.6 for c in chosen):
                continue
            chosen.append(route)
            if len(chosen) >= limit:
                break
        return chosen


def _overlap(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from one point to another, in degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass(bearing: float) -> str:
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((bearing + 22.5) // 45) % 8]


def route_geojson(graph: WalkGraph, route: Route) -> dict:
    """The route as a GeoJSON LineString, plus per-surface segments for styling."""
    coords: list[list[float]] = []
    for edge, reverse in route.legs:
        pts = graph.edge_coords(edge, bool(reverse))
        if coords and pts and coords[-1] == pts[0]:
            coords.extend(pts[1:])
        else:
            coords.extend(pts)

    # Separate features per surface let the map draw path vs road differently,
    # which is the visual answer to "will I be walking on the road".
    segments: list[dict] = []
    current: list[list[float]] = []
    current_surface: str | None = None
    for edge, reverse in route.legs:
        surface = graph.surface_of(edge)
        pts = graph.edge_coords(edge, bool(reverse))
        if surface != current_surface:
            if current and current_surface:
                segments.append({"surface": current_surface, "coordinates": current})
            current = list(pts)
            current_surface = surface
        else:
            current.extend(pts[1:] if current and pts and current[-1] == pts[0] else pts)
    if current and current_surface:
        segments.append({"surface": current_surface, "coordinates": current})

    return {"type": "LineString", "coordinates": coords, "segments": segments}


def elevation_profile(graph: WalkGraph, route: Route) -> list[dict]:
    """Cumulative distance vs elevation along the route, for the profile chart."""
    out = []
    running = 0.0
    for i, node in enumerate(route.nodes):
        if i > 0:
            running += graph.edge_len[route.legs[i - 1][0]]
        out.append({"m": round(running), "ele": round(graph.elevation(node), 1)})
    # A point per node is far more resolution than a chart needs; thin it out.
    if len(out) > 200:
        step = len(out) / 200.0
        out = [out[int(i * step)] for i in range(200)] + [out[-1]]
    return out
