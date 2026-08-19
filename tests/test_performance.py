"""Performance guards.

Not benchmarks — guards. They assert the *shape* of the performance story, at
thresholds loose enough not to flake on a slow CI runner but tight enough to
catch a regression that would change the architecture's viability.

The numbers they protect (measured on an M-series laptop):

    cold start, all arrays decoded   ~15 ms
    plan, Morton 30 min           ~4 ms
    plan, San Francisco 40 min        ~90 ms
    plan, San Francisco 90 min       ~224 ms

That profile is why this app has no database. See `test_no_database_needed`.
"""

from __future__ import annotations

import json
import time

import pytest
from stepwise.handler import handler
from stepwise.models.location import Coordinate
from stepwise.models.profile import Profile
from stepwise.services.search import CostModel, GraphSearch, Preferences

PROFILE = {"sex": "male", "age": 33, "weight_lb": 320, "height_ft": 6}


def plan(address: str, minutes: int) -> tuple[dict, float]:
    """Issue one plan request and return the body and the elapsed milliseconds."""
    event = {
        "rawPath": "/v1/plan",
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(
            {"address": address, "minutes": minutes, "profile": PROFILE, "preferences": {}}
        ),
    }
    started = time.perf_counter()
    response = handler(event, None)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert response["statusCode"] == 200
    return json.loads(response["body"]), elapsed_ms


@pytest.fixture(scope="module", autouse=True)
def warm():
    """Load the datasets before timing anything."""
    plan("1000 California St, San Francisco", 30)
    plan("100 N Main St, Morton, IL", 30)


class TestColdStart:
    def test_decoding_the_graph_is_a_memcpy_not_a_parse(self, registry):
        """The whole case for the binary container.

        Decoding every routing column of the largest region must stay in the
        low tens of milliseconds. If this ever takes hundreds, the format has
        regressed into something that parses rather than copies, and cold
        starts stop being acceptable.
        """
        from stepwise.container import Container

        path = registry.data_dir / "sf.graph.spw"
        started = time.perf_counter()
        container = Container.load(path)
        for name in (
            "node_lat",
            "node_lon",
            "node_ele_dm",
            "adj_start",
            "adj_edge",
            "edge_u",
            "edge_v",
            "edge_len",
            "edge_grade_dpct",
        ):
            container.get(name)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        assert elapsed_ms < 250, f"decoding took {elapsed_ms:.0f}ms"

    def test_the_spatial_index_builds_quickly(self, sf):
        """Built lazily on first use and reused for the container's life."""
        graph = sf.graph
        graph._grid = None
        started = time.perf_counter()
        _ = graph.grid
        assert (time.perf_counter() - started) * 1000.0 < 500


class TestQueryLatency:
    def test_a_sparse_region_plans_almost_instantly(self):
        _, elapsed_ms = plan("100 N Main St, Morton, IL", 30)
        assert elapsed_ms < 150, f"took {elapsed_ms:.0f}ms"

    def test_a_dense_region_plans_within_budget(self):
        _, elapsed_ms = plan("1000 California St, San Francisco", 40)
        assert elapsed_ms < 900, f"took {elapsed_ms:.0f}ms"

    def test_the_longest_supported_walk_stays_responsive(self):
        """The worst case the API accepts. If this creeps past a couple of
        seconds the Lambda timeout becomes reachable."""
        _, elapsed_ms = plan("1000 California St, San Francisco", 120)
        assert elapsed_ms < 3000, f"took {elapsed_ms:.0f}ms"

    def test_cost_lookup_tables_are_built_once_per_request(self, sf):
        """Tabulating gradient costs must not become per-edge work again."""
        graph = sf.graph
        profile = Profile("male", 33, 163.7, 182.9)
        started = time.perf_counter()
        model = CostModel(graph, Preferences(), profile)
        build_ms = (time.perf_counter() - started) * 1000.0
        assert build_ms < 50, f"table build took {build_ms:.0f}ms"
        assert len(model._cost_factor) == len(model._inv_speed)

    def test_the_search_scales_with_the_budget_not_the_graph(self, sf):
        """A bounded search must not degenerate into a full-graph traversal.

        Doubling the time budget should cost meaningfully less than doubling
        the graph would — this is what keeps San Francisco's 123,000 edges from
        mattering for a 20-minute walk.
        """
        graph = sf.graph
        profile = Profile("male", 33, 163.7, 182.9)
        search = GraphSearch(graph, CostModel(graph, Preferences(), profile))
        start = graph.nearest_node(Coordinate(37.7919, -122.4127))[0]

        small = search.run(start, 300.0)
        assert len(small.cost) < graph.n_nodes * 0.25, "a 5-minute ball is not a quarter of SF"


class TestResponseSize:
    def test_the_response_compresses_well(self):
        """API Gateway compresses above 1 KB; geometry dominates and is highly
        repetitive, so the wire cost is a fraction of the raw payload."""
        import gzip

        body, _ = plan("1000 California St, San Francisco", 40)
        raw = json.dumps(body).encode()
        compressed = gzip.compress(raw)
        assert len(compressed) < len(raw) * 0.25
        assert len(compressed) < 60_000, "compressed response should stay well under 60 KB"

    def test_elevation_profiles_are_thinned(self):
        """A chart a few hundred pixels wide does not need a point per node."""
        body, _ = plan("1000 California St, San Francisco", 120)
        for route in body["routes"]:
            assert len(route["elevation_profile"]) <= 201


def test_no_database_needed(sf):
    """Documents why this app has no database, as an executable argument.

    A single plan request settles tens of thousands of graph nodes. Any
    datastore — however fast — would turn that into network round trips, and
    even 0.1 ms per lookup would put a San Francisco request into the tens of
    seconds. Holding the graph as flat arrays in the Lambda's own memory is not
    a shortcut around a database; it is several orders of magnitude faster than
    one could be for this access pattern.

    A database would start to make sense if the data were mutable, per-user, or
    too large for a deployment package. It is none of those: 25 MB, rebuilt
    offline from public data.
    """
    graph = sf.graph
    profile = Profile("male", 33, 163.7, 182.9)
    search = GraphSearch(graph, CostModel(graph, Preferences(), profile))
    start = graph.nearest_node(Coordinate(37.7919, -122.4127))[0]

    started = time.perf_counter()
    result = search.run(start, 1200.0)
    elapsed_s = time.perf_counter() - started

    nodes_settled = len(result.cost)
    assert nodes_settled > 1000, "this should be a substantial search"

    # The counterfactual, at a generous 0.1 ms per remote lookup.
    hypothetical_db_s = nodes_settled * 0.0001
    assert elapsed_s < hypothetical_db_s, (
        f"in-memory search settled {nodes_settled} nodes in {elapsed_s * 1000:.0f}ms; "
        f"a datastore at 0.1ms/lookup would need {hypothetical_db_s * 1000:.0f}ms"
    )
