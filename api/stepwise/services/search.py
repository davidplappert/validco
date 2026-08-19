"""Graph search: the cost model and the bounded Dijkstra built on it.

The two ideas worth knowing before reading the code:

**Cost is metabolic, not geometric.** An edge's base cost is Minetti's energy
cost for its actual gradient, multiplied by preference weights for surface type.
So the router avoids a hill because climbing it is genuinely expensive, and
avoids the roadway because the walker said they would rather be on a path —
two different kinds of "cost", kept deliberately separate.

**The search is bounded by predicted time, not distance.** A 40-minute request
on Nob Hill used to return an hour of climbing, because a kilometre uphill is
not a kilometre. Accumulating seconds with the same physiology that produces the
final estimate means the budget the search enforces and the number shown to the
user cannot disagree.
"""

from __future__ import annotations

import heapq
import logging
import math
from typing import Any

from ..config import FLAG_BUSY, FLAG_STEPS, FLAG_UNPAVED, SURFACE_COST, SURFACE_CROSSING
from ..datasets.graph import WalkGraph
from ..models.profile import Profile
from ..physiology.energy import DEFAULT_COST_MODEL, GradientCostModel

LOG = logging.getLogger(__name__)

#: Seconds lost at a signalised crossing, averaged over arrival times.
CROSSING_PAUSE_S = 12.0

#: Multiplier applied to an already-walked edge on the return leg. High enough
#: to push the route onto a different street, low enough that a dead-end
#: peninsula can still be walked back out of.
REUSE_PENALTY = 4.0


class Preferences:
    """What the walker wants beyond a time budget."""

    __slots__ = ("prefer_paths", "avoid_hills", "avoid_stairs", "avoid_busy_roads", "prefer_green")

    def __init__(
        self,
        prefer_paths: bool = True,
        avoid_hills: bool = False,
        avoid_stairs: bool = False,
        avoid_busy_roads: bool = True,
        prefer_green: bool = False,
    ):
        """Store the five preference toggles the API exposes."""
        self.prefer_paths = prefer_paths
        self.avoid_hills = avoid_hills
        self.avoid_stairs = avoid_stairs
        self.avoid_busy_roads = avoid_busy_roads
        self.prefer_green = prefer_green

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Preferences:
        """Build from request JSON, defaulting anything absent."""
        raw = raw or {}
        return cls(
            prefer_paths=bool(raw.get("prefer_paths", True)),
            avoid_hills=bool(raw.get("avoid_hills", False)),
            avoid_stairs=bool(raw.get("avoid_stairs", False)),
            avoid_busy_roads=bool(raw.get("avoid_busy_roads", True)),
            prefer_green=bool(raw.get("prefer_green", False)),
        )

    @property
    def hill_exponent(self) -> float:
        """Exponent on the gradient cost term.

        Raising it above 1 makes hills disproportionately expensive, so the
        router will accept a materially longer flat detour to avoid one.
        """
        return 2.2 if self.avoid_hills else 1.0

    def to_dict(self) -> dict[str, bool]:
        """Serialise for echoing back in the API response."""
        return {name: getattr(self, name) for name in self.__slots__}


class CostModel:
    """Turns an edge traversal into a routing cost and a predicted duration.

    Both come out of one gradient computation. The inner loop of Dijkstra runs
    millions of times per request, and computing the grade twice per edge was
    measurable, so the two results are returned together rather than from two
    methods.
    """

    def __init__(
        self,
        graph: WalkGraph,
        preferences: Preferences,
        profile: Profile,
        gradient_cost: GradientCostModel | None = None,
    ):
        """Bind the graph, the walker and their preferences.

        Surface multipliers are resolved once here rather than branched on per
        edge. When the walker has not asked to prefer paths the weights are
        compressed toward neutral instead of discarded, so a route still avoids
        walking down the middle of a road when a sidewalk exists.
        """
        self.graph = graph
        self.preferences = preferences
        self.profile = profile
        self.gradient_cost = gradient_cost or DEFAULT_COST_MODEL
        self.flat_cost = self.gradient_cost.cost_j_per_kg_m(0.0)
        self.hill_exponent = preferences.hill_exponent

        if preferences.prefer_paths:
            self.surface_multiplier = dict(SURFACE_COST)
        else:
            self.surface_multiplier = {
                surface: 1.0 + (weight - 1.0) * 0.25 for surface, weight in SURFACE_COST.items()
            }

    def metrics(self, edge: int, reverse: bool) -> tuple[float, float]:
        """Return ``(routing_cost, seconds)`` for traversing one edge.

        A cost of infinity means the edge is impassable under these preferences
        — currently only stairs, when the walker has excluded them.
        """
        graph = self.graph
        length = graph.edge_len[edge]
        u, v = graph.edge_u[edge], graph.edge_v[edge]
        if reverse:
            u, v = v, u

        grade = (graph.elevation(v) - graph.elevation(u)) / length if length > 0 else 0.0
        gradient_multiplier = (
            self.gradient_cost.cost_j_per_kg_m(grade) / self.flat_cost
        ) ** self.hill_exponent

        cost = length * gradient_multiplier * self.surface_multiplier[graph.edge_surface[edge]]
        seconds = length / self.profile.speed_on_grade_ms(grade)

        flags = graph.edge_flags[edge]
        if flags & FLAG_STEPS:
            if self.preferences.avoid_stairs:
                return math.inf, math.inf
            cost *= 1.5
        if self.preferences.avoid_busy_roads and (flags & FLAG_BUSY):
            cost *= 1.4
        if flags & FLAG_UNPAVED:
            cost *= 1.15 if self.preferences.prefer_paths else 1.05
        if graph.edge_surface[edge] == SURFACE_CROSSING:
            # Waiting at the light is part of how long the walk takes.
            seconds += CROSSING_PAUSE_S
        return cost, seconds


class SearchResult:
    """What one bounded Dijkstra reached, and how it got there."""

    __slots__ = ("cost", "metres", "seconds", "previous", "source", "settled")

    def __init__(self, source: int):
        """Start an empty result rooted at ``source``."""
        self.source = source
        self.cost: dict[int, float] = {source: 0.0}
        self.metres: dict[int, float] = {source: 0.0}
        self.seconds: dict[int, float] = {source: 0.0}
        self.previous: dict[int, tuple[int, int, bool]] = {}
        self.settled = 0

    def reached(self, node: int) -> bool:
        """Whether the search found a path to a node."""
        return node in self.cost

    def trace(self, target: int) -> list[tuple[int, bool]]:
        """Walk the predecessor chain back, returning legs in travel order.

        Returns an empty list if the target was never reached. The iteration
        guard is a safety net against a corrupted predecessor map; it should
        never fire, and it logs loudly if it does.
        """
        legs: list[tuple[int, bool]] = []
        node = target
        for _ in range(100_000):
            if node == self.source:
                legs.reverse()
                return legs
            step = self.previous.get(node)
            if step is None:
                return []
            from_node, edge, reverse = step
            legs.append((edge, reverse))
            node = from_node
        LOG.error("trace exceeded iteration guard target=%d source=%d", target, self.source)
        return []


class GraphSearch:
    """Bounded Dijkstra over the walking graph."""

    def __init__(self, graph: WalkGraph, cost_model: CostModel):
        """Bind the graph and the cost model to search with."""
        self.graph = graph
        self.cost_model = cost_model

    def run(
        self,
        source: int,
        max_seconds: float,
        *,
        penalised_edges: set[int] | None = None,
        target: int | None = None,
    ) -> SearchResult:
        """Explore outward from ``source`` within a walking-time budget.

        ``penalised_edges`` multiplies the cost of edges already used, which is
        how the return leg of a loop is pushed onto different streets without
        ever explicitly searching for a cycle. ``target`` stops the search early
        once that node is settled.
        """
        graph = self.graph
        metrics = self.cost_model.metrics
        result = SearchResult(source)
        heap: list[tuple[float, int]] = [(0.0, source)]
        settled: set[int] = set()

        edge_u, edge_v, edge_len = graph.edge_u, graph.edge_v, graph.edge_len
        adj_start, adj_edge = graph.adj_start, graph.adj_edge
        cost, metres, seconds, previous = (
            result.cost,
            result.metres,
            result.seconds,
            result.previous,
        )

        while heap:
            current_cost, node = heapq.heappop(heap)
            if node in settled:
                continue
            settled.add(node)
            if target is not None and node == target:
                break

            base_seconds = seconds[node]
            if base_seconds > max_seconds:
                continue
            base_metres = metres[node]

            for k in range(adj_start[node], adj_start[node + 1]):
                packed = adj_edge[k]
                edge = packed >> 1
                reverse = bool(packed & 1)
                nxt = edge_u[edge] if reverse else edge_v[edge]
                if nxt in settled:
                    continue

                step_cost, step_seconds = metrics(edge, reverse)
                if step_cost == math.inf or base_seconds + step_seconds > max_seconds:
                    continue
                if penalised_edges and edge in penalised_edges:
                    step_cost *= REUSE_PENALTY

                candidate = current_cost + step_cost
                if candidate < cost.get(nxt, math.inf):
                    cost[nxt] = candidate
                    metres[nxt] = base_metres + edge_len[edge]
                    seconds[nxt] = base_seconds + step_seconds
                    previous[nxt] = (node, edge, reverse)
                    heapq.heappush(heap, (candidate, nxt))

        result.settled = len(settled)
        LOG.debug(
            "dijkstra source=%d settled=%d reached=%d budget_s=%.0f",
            source,
            result.settled,
            len(cost),
            max_seconds,
        )
        return result
