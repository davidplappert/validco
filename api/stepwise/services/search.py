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

from ..config import (
    FLAG_BUSY,
    FLAG_STEPS,
    FLAG_UNPAVED,
    GRADE_LIMIT_DPCT,
    GRADE_SCALE,
    GRADE_TABLE_SIZE,
    SURFACE_COST,
    SURFACE_CROSSING,
    SURFACES,
)
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
        """Bind the graph and the walker, and precompute the lookup tables.

        Everything that depends only on gradient is tabulated here, once per
        request, indexed by the same decipercent gradient the graph stores per
        edge. That turns the routing inner loop — which runs several hundred
        thousand times for a long request — from "evaluate a fifth-order
        polynomial, an exponential, a power and two divisions" into two array
        indexes and two multiplications.

        Profiling a 90-minute San Francisco plan attributed 38% of wall time to
        this function before the change.
        """
        self.graph = graph
        self.preferences = preferences
        self.profile = profile
        self.gradient_cost = gradient_cost or DEFAULT_COST_MODEL
        self.flat_cost = self.gradient_cost.cost_j_per_kg_m(0.0)
        self.hill_exponent = preferences.hill_exponent

        if preferences.prefer_paths:
            weights = dict(SURFACE_COST)
        else:
            # Compressed toward neutral rather than discarded: a walker who has
            # not asked for paths still does not want the middle of a road.
            weights = {
                surface: 1.0 + (weight - 1.0) * 0.25 for surface, weight in SURFACE_COST.items()
            }
        self.surface_multiplier = weights
        # Indexed by surface code, so the inner loop does not hash a dict.
        self._surface_by_code = [weights[code] for code in range(len(SURFACES))]

        self._cost_factor, self._inv_speed = self._build_tables()

        # Bound locals for the hot path.
        self._avoid_stairs = preferences.avoid_stairs
        self._avoid_busy = preferences.avoid_busy_roads
        self._unpaved_penalty = 1.15 if preferences.prefer_paths else 1.05

    def _build_tables(self) -> tuple[list[float], list[float]]:
        """Tabulate cost and inverse speed against gradient.

        Inverse speed rather than speed, so the inner loop multiplies instead of
        dividing. 901 entries at 0.1% gradient resolution — finer than the ~10 m
        DEM the gradients were derived from, so the table introduces no error
        the data did not already have.
        """
        cost_factor: list[float] = []
        inv_speed: list[float] = []
        for index in range(GRADE_TABLE_SIZE):
            grade = (index - GRADE_LIMIT_DPCT) / GRADE_SCALE
            factor = self.gradient_cost.cost_j_per_kg_m(grade) / self.flat_cost
            cost_factor.append(factor**self.hill_exponent)
            inv_speed.append(1.0 / self.profile.speed_on_grade_ms(grade))
        return cost_factor, inv_speed

    def metrics(self, edge: int, reverse: bool) -> tuple[float, float]:
        """Return ``(routing_cost, seconds)`` for traversing one edge.

        A cost of infinity means the edge is impassable under these preferences
        — currently only stairs, when the walker has excluded them.
        """
        graph = self.graph
        length = graph.edge_len[edge]

        # The stored gradient is for u -> v; walking the other way negates it.
        grade_dpct = graph.edge_grade_dpct[edge]
        index = (GRADE_LIMIT_DPCT - grade_dpct) if reverse else (GRADE_LIMIT_DPCT + grade_dpct)

        surface = graph.edge_surface[edge]
        cost = length * self._cost_factor[index] * self._surface_by_code[surface]
        seconds = length * self._inv_speed[index]

        flags = graph.edge_flags[edge]
        if flags:
            if flags & FLAG_STEPS:
                if self._avoid_stairs:
                    return math.inf, math.inf
                cost *= 1.5
            if self._avoid_busy and (flags & FLAG_BUSY):
                cost *= 1.4
            if flags & FLAG_UNPAVED:
                cost *= self._unpaved_penalty
        if surface == SURFACE_CROSSING:
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
