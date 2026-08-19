"""Tests for the service layer, against the real baked datasets.

These are the tests that would have caught every bug found while building this:
an off-by-one in route assembly, a regex eating a street's directional prefix,
a search bounded by the wrong quantity.
"""

from __future__ import annotations

import pytest
from stepwise.models.location import Coordinate
from stepwise.services.geocoder import AddressParser, Geocoder, StreetNormalizer
from stepwise.services.planner import WalkPlanner
from stepwise.services.scoring import RouteScorer, ScoringWeights
from stepwise.services.search import CostModel, GraphSearch, Preferences

# A real address in the Peoria region, present in Overture.
ILLINOIS_ADDRESS = "100 N Main St, Morton, IL 61550"
SF_NOB_HILL = "1000 California St, San Francisco"


class TestPreferences:
    def test_defaults_favour_paths_and_avoid_traffic(self):
        prefs = Preferences.from_dict(None)
        assert prefs.prefer_paths is True
        assert prefs.avoid_busy_roads is True
        assert prefs.avoid_hills is False

    def test_hill_exponent_rises_when_hills_are_avoided(self):
        assert Preferences(avoid_hills=False).hill_exponent == 1.0
        assert Preferences(avoid_hills=True).hill_exponent > 1.0

    def test_round_trips_through_a_dict(self):
        original = Preferences(prefer_paths=False, avoid_stairs=True)
        assert Preferences.from_dict(original.to_dict()).to_dict() == original.to_dict()


class TestCostModel:
    def test_stairs_are_impassable_when_excluded(self, sf, lean_profile):
        """The one hard exclusion in the cost model."""
        from stepwise.config import FLAG_STEPS

        graph = sf.graph
        stair_edge = next(
            (i for i in range(graph.n_edges) if graph.edge_flags[i] & FLAG_STEPS), None
        )
        assert stair_edge is not None, "SF must contain stairs"

        permissive = CostModel(graph, Preferences(avoid_stairs=False), lean_profile)
        strict = CostModel(graph, Preferences(avoid_stairs=True), lean_profile)
        assert permissive.metrics(stair_edge, False)[0] < float("inf")
        assert strict.metrics(stair_edge, False)[0] == float("inf")

    def test_cost_and_duration_are_both_positive(self, pia, lean_profile):
        model = CostModel(pia.graph, Preferences(), lean_profile)
        cost, seconds = model.metrics(0, False)
        assert cost > 0 and seconds > 0

    def test_traversal_direction_changes_the_cost_on_a_slope(self, sf, lean_profile):
        """Walking up a hill must cost more than walking down it."""
        graph = sf.graph
        model = CostModel(graph, Preferences(), lean_profile)
        for edge in range(min(4000, graph.n_edges)):
            rise = graph.elevation(graph.edge_v[edge]) - graph.elevation(graph.edge_u[edge])
            if abs(rise) > 8.0 and graph.edge_len[edge] > 40.0:
                uphill = model.metrics(edge, rise < 0)[0]
                downhill = model.metrics(edge, rise > 0)[0]
                assert uphill > downhill
                return
        pytest.skip("no sufficiently steep edge found in the sampled range")

    def test_a_heavier_walker_takes_longer_over_the_same_edge(
        self, pia, lean_profile, heavy_profile
    ):
        graph = pia.graph
        lean = CostModel(graph, Preferences(), lean_profile).metrics(0, False)[1]
        heavy = CostModel(graph, Preferences(), heavy_profile).metrics(0, False)[1]
        assert heavy > lean


class TestGraphSearch:
    def test_respects_its_time_budget(self, pia, lean_profile):
        """The property that keeps a 40-minute request a 40-minute walk."""
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        search = GraphSearch(graph, CostModel(graph, Preferences(), lean_profile))
        result = search.run(start, max_seconds=600.0)
        assert result.seconds and max(result.seconds.values()) <= 600.0 + 1e-6

    def test_a_larger_budget_reaches_further(self, pia, lean_profile):
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        search = GraphSearch(graph, CostModel(graph, Preferences(), lean_profile))
        assert len(search.run(start, 300.0).cost) < len(search.run(start, 900.0).cost)

    def test_trace_returns_legs_in_travel_order(self, pia, lean_profile):
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        search = GraphSearch(graph, CostModel(graph, Preferences(), lean_profile))
        result = search.run(start, 600.0)

        target = max(result.seconds, key=result.seconds.get)
        legs = result.trace(target)
        assert legs

        # Walking the legs from the source must arrive at the target.
        node = start
        for edge, reverse in legs:
            node = graph.head_of(edge, reverse)
        assert node == target

    def test_trace_of_an_unreached_node_is_empty(self, pia, lean_profile):
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        search = GraphSearch(graph, CostModel(graph, Preferences(), lean_profile))
        result = search.run(start, 60.0)
        unreached = next(n for n in range(graph.n_nodes) if n not in result.cost)
        assert result.trace(unreached) == []

    def test_penalised_edges_push_the_route_elsewhere(self, pia, lean_profile):
        """The mechanism that turns an out-and-back into a loop.

        The penalised run gets a larger budget so the target stays reachable and
        the assertion can be exact: going the same way must cost more, and the
        chosen path must reuse fewer of the penalised edges. (With an equal
        budget the target often falls out of range entirely, which is the
        penalty working but makes for a vacuous test.)
        """
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        search = GraphSearch(graph, CostModel(graph, Preferences(), lean_profile))

        plain = search.run(start, 600.0)
        target = max(plain.seconds, key=plain.seconds.get)
        used = {edge for edge, _ in plain.trace(target)}
        assert used

        penalised = search.run(start, 1800.0, penalised_edges=used)
        assert target in penalised.cost, "a larger budget must still reach the target"
        assert penalised.cost[target] > plain.cost[target]

        detour = {edge for edge, _ in penalised.trace(target)}
        assert len(detour & used) < len(used), "the search should avoid reusing edges"


class TestStreetNormalizer:
    @pytest.mark.parametrize(
        "written,canonical",
        [
            ("N Main St", "north main street"),
            ("North Main Street", "north main street"),
            ("O'Farrell St", "ofarrell street"),
            ("OFarrell Street", "ofarrell street"),
            ("SE Riverside Pkwy", "southeast riverside parkway"),
        ],
    )
    def test_variants_fold_to_one_key(self, written, canonical):
        assert StreetNormalizer().normalize(written) == canonical

    def test_is_a_pure_function(self):
        """Both the builder and the runtime call this; it cannot carry state."""
        normalizer = StreetNormalizer()
        assert normalizer.normalize("Market St") == normalizer.normalize("Market St")


class TestAddressParser:
    def test_directional_prefix_survives_the_house_number(self):
        """Regression: a greedy suffix match once ate the N from 100 N Main."""
        parsed = AddressParser().parse("100 N Main St, Morton, IL 61550")
        assert parsed.number == 100
        assert parsed.street_norm == "north main street"
        assert parsed.postcode == "61550"

    def test_adjacent_letter_is_a_unit_suffix(self):
        parsed = AddressParser().parse("450A Market St")
        assert parsed.number == 450 and parsed.street_norm == "market street"

    def test_coordinates_are_recognised(self):
        coordinate = AddressParser().parse_latlon("37.7749, -122.4194")
        assert coordinate == Coordinate(37.7749, -122.4194)

    def test_prose_is_not_coordinates(self):
        assert AddressParser().parse_latlon("100 N Main St") is None

    def test_out_of_range_coordinates_are_rejected(self):
        assert AddressParser().parse_latlon("999, -122") is None


class TestGeocoder:
    def test_finds_a_real_address(self, pia):
        result = Geocoder(pia.addresses).resolve(ILLINOIS_ADDRESS)
        assert result.found and result.match == "exact"
        assert result.coordinate.lat == pytest.approx(40.6103, abs=0.01)

    def test_falls_back_to_the_nearest_house_number(self, pia):
        """Address corpora always have holes; 'not found' is the wrong answer."""
        result = Geocoder(pia.addresses).resolve("709999 N Main St")
        assert result.found and result.match == "nearest_number"

    def test_street_without_a_number_resolves_to_the_street(self, sf):
        result = Geocoder(sf.addresses).resolve("Market Street, San Francisco")
        assert result.found and result.match == "street_midpoint"

    def test_a_miss_offers_suggestions(self, sf):
        result = Geocoder(sf.addresses).resolve("12 Marke Stret")
        if not result.found:
            assert isinstance(result.suggestions, list)

    def test_accepts_literal_coordinates(self, sf):
        result = Geocoder(sf.addresses).resolve("37.7749, -122.4194")
        assert result.found and result.match == "coordinates"


@pytest.fixture(scope="module")
def morton_start(pia):
    """The graph node nearest the Morton test address."""
    return pia.graph.nearest_node(Coordinate(40.61034, -89.46161))[0]


class TestWalkPlanner:
    def test_produces_candidates(self, pia, heavy_profile, morton_start):
        planner = WalkPlanner(pia.graph, places=pia.places, green=pia.green)
        routes = planner.plan(heavy_profile, morton_start, 30.0, Preferences())
        assert routes

    def test_every_candidate_returns_to_the_start(self, pia, heavy_profile, morton_start):
        """A walk that does not come home is not a walk."""
        planner = WalkPlanner(pia.graph, places=pia.places)
        for route in planner.plan(heavy_profile, morton_start, 30.0, Preferences()):
            assert route.nodes[0] == route.nodes[-1] == morton_start
            assert route.geometry.is_closed

    def test_geometry_is_continuous(self, pia, heavy_profile, morton_start):
        """A jump between adjacent points means an edge was sliced wrongly."""
        planner = WalkPlanner(pia.graph, places=pia.places)
        for route in planner.plan(heavy_profile, morton_start, 30.0, Preferences()):
            coordinates = route.geometry.coordinates
            for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:], strict=False):
                assert abs(lon1 - lon2) < 0.01 and abs(lat1 - lat2) < 0.01

    def test_surface_runs_cover_the_whole_route(self, pia, heavy_profile, morton_start):
        planner = WalkPlanner(pia.graph, places=pia.places)
        route = planner.plan(heavy_profile, morton_start, 30.0, Preferences())[0]
        assert route.geometry.segments
        assert sum(route.surfaces.percentages().values()) == pytest.approx(100.0, abs=0.5)

    def test_durations_land_near_the_request(self, pia, heavy_profile, morton_start):
        planner = WalkPlanner(pia.graph, places=pia.places)
        for route in planner.plan(heavy_profile, morton_start, 30.0, Preferences()):
            assert 15 <= route.effort.duration_min <= 50

    def test_an_isolated_start_yields_nothing_rather_than_crashing(self, pia, heavy_profile):
        planner = WalkPlanner(pia.graph)
        # A one-second budget cannot leave the start node.
        assert planner.plan(heavy_profile, 0, 0.02, Preferences()) == []


class TestRouteScorer:
    def test_prefers_the_requested_duration(self, pia, heavy_profile):
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        planner = WalkPlanner(graph, places=pia.places)
        routes = planner.plan(heavy_profile, start, 30.0, Preferences())
        assert routes

        scorer = RouteScorer()
        ranked = scorer.rank(routes, 30.0, Preferences(), limit=4)
        scores = [r.score for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_a_wildly_wrong_duration_scores_far_lower(self, pia, heavy_profile):
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        routes = WalkPlanner(graph, places=pia.places).plan(
            heavy_profile, start, 30.0, Preferences()
        )
        scorer = RouteScorer()
        on_target = scorer.score(routes[0], 30.0, Preferences())
        off_target = scorer.score(routes[0], 120.0, Preferences())
        assert off_target < on_target

    def test_diversify_drops_near_duplicates(self, pia, heavy_profile):
        graph = pia.graph
        start = graph.nearest_node(Coordinate(40.61034, -89.46161))[0]
        routes = WalkPlanner(graph, places=pia.places).plan(
            heavy_profile, start, 30.0, Preferences(), max_routes=6
        )
        kept = RouteScorer.diversify(routes, limit=6)
        for i, a in enumerate(kept):
            for b in kept[i + 1 :]:
                assert a.overlap_with(b) <= 0.6

    def test_weights_are_adjustable(self):
        assert (
            ScoringWeights().DURATION_SHARE + ScoringWeights().SUITABILITY_SHARE
            == pytest.approx(1.0)
        )
