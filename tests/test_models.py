"""Tests for the domain models."""

from __future__ import annotations

import pytest
from stepwise.models.effort import WalkEffort, WalkSegment
from stepwise.models.location import Coordinate, GeocodeResult, Origin, compass
from stepwise.models.region import BoundingBox, Region
from stepwise.models.route import (
    ElevationProfile,
    Leg,
    RouteGeometry,
    SurfaceBreakdown,
    WalkSegmentBuilder,
)
from stepwise.models.suitability import (
    DurationRule,
    GradeToleranceRule,
    IntensityRule,
    SuitabilityAssessment,
    TotalClimbRule,
)


class TestCoordinate:
    def test_distance_matches_a_known_pair(self):
        """SF City Hall to the Ferry Building is about 2.4 km."""
        a = Coordinate(37.7793, -122.4193)
        b = Coordinate(37.7955, -122.3937)
        assert a.distance_to(b) == pytest.approx(2600, rel=0.15)

    def test_distance_is_symmetric(self):
        a, b = Coordinate(37.77, -122.41), Coordinate(40.91, -89.50)
        assert a.distance_to(b) == pytest.approx(b.distance_to(a))

    def test_distance_to_self_is_zero(self):
        a = Coordinate(37.77, -122.41)
        assert a.distance_to(a) == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.parametrize(
        "target,expected",
        [
            ((38.77, -122.41), "N"),
            ((36.77, -122.41), "S"),
            ((37.77, -121.41), "E"),
            ((37.77, -123.41), "W"),
        ],
    )
    def test_bearing_points_the_right_way(self, target, expected):
        origin = Coordinate(37.77, -122.41)
        assert compass(origin.bearing_to(Coordinate(*target))) == expected

    def test_geojson_order_is_lon_lat(self):
        """GeoJSON is the one place the order flips, so it is asserted."""
        assert Coordinate(37.77, -122.41).to_geojson() == [-122.41, 37.77]


class TestBoundingBox:
    def test_contains(self):
        box = BoundingBox(-122.53, 37.69, -122.34, 37.84)
        assert box.contains(Coordinate(37.7749, -122.4194))
        assert not box.contains(Coordinate(40.6103, -89.4616))

    def test_round_trips_through_a_list(self):
        values = [-122.53, 37.69, -122.34, 37.84]
        assert BoundingBox.from_list(values).to_list() == values


class TestRegion:
    def test_serialises_for_the_api(self):
        region = Region(
            key="sf",
            label="San Francisco, CA",
            center=[37.77, -122.41],
            bbox=[-122.53, 37.69, -122.34, 37.84],
            n_nodes=1,
            n_edges=2,
            n_addresses=3,
            n_places=4,
        )
        payload = region.to_dict()
        assert payload["key"] == "sf"
        assert payload["center"] == [37.77, -122.41]
        assert payload["n_edges"] == 2


class TestGeocodeResult:
    def test_a_hit_carries_its_coordinates_and_match_kind(self):
        result = GeocodeResult.hit(Coordinate(40.9, -89.5), "100 N Main St", "exact")
        payload = result.to_dict()
        assert payload["found"] is True
        assert payload["match"] == "exact"
        assert payload["lat"] == 40.9

    def test_a_miss_carries_suggestions(self):
        result = GeocodeResult.miss("no such street", ["Market Street"])
        payload = result.to_dict()
        assert payload["found"] is False
        assert payload["suggestions"] == ["Market Street"]


class TestOrigin:
    def test_reports_both_the_request_and_the_snap(self):
        """The snap distance is how the app admits it moved the start point."""
        origin = Origin(Coordinate(40.61034, -89.46161), Coordinate(40.6104, -89.4612), 42.1)
        payload = origin.to_dict()
        assert payload["lat"] == 40.61034
        assert payload["snapped_lat"] == 40.6104
        assert payload["snap_distance_m"] == 42


class TestWalkSegment:
    def test_grade_is_rise_over_run(self):
        assert WalkSegment(100.0, 10.0).grade == pytest.approx(0.10)

    def test_zero_length_has_no_grade(self):
        assert WalkSegment(0.0, 10.0).grade == 0.0


class TestSurfaceBreakdown:
    def test_percentages_sum_to_a_hundred(self):
        breakdown = SurfaceBreakdown({"road": 800.0, "sidewalk": 200.0})
        assert sum(breakdown.percentages().values()) == pytest.approx(100.0)
        assert breakdown.share_of("road") == pytest.approx(80.0)

    def test_unknown_surface_is_zero(self):
        assert SurfaceBreakdown({"road": 100.0}).share_of("path") == 0.0

    def test_empty_breakdown_does_not_divide_by_zero(self):
        assert SurfaceBreakdown({}).percentages() == {}


class TestElevationProfile:
    def test_short_profiles_pass_through(self):
        profile = ElevationProfile([(0.0, 10.0), (100.0, 12.0)])
        assert len(profile.thinned()) == 2

    def test_long_profiles_are_thinned_but_keep_the_end(self):
        """The final point must survive, or the chart stops short of the route."""
        points = [(float(i), float(i % 20)) for i in range(1000)]
        thinned = ElevationProfile(points).thinned(limit=50)
        assert len(thinned) <= 51
        assert thinned[0]["m"] == 0
        assert thinned[-1]["m"] == 999


class TestRouteGeometry:
    def test_detects_a_closed_loop(self):
        geometry = RouteGeometry([[-122.4, 37.7], [-122.5, 37.8], [-122.4, 37.7]], [])
        assert geometry.is_closed is True

    def test_detects_an_open_line(self):
        geometry = RouteGeometry([[-122.4, 37.7], [-122.5, 37.8]], [])
        assert geometry.is_closed is False

    def test_a_degenerate_line_is_not_closed(self):
        assert RouteGeometry([[-122.4, 37.7]], []).is_closed is False


class FakeGraph:
    """Minimal graph stand-in for testing segment assembly in isolation."""

    def __init__(self, lengths, elevations):
        self.edge_len = lengths
        self._elevations = elevations

    def elevation(self, node):
        return self._elevations[node]


class TestWalkSegmentBuilder:
    def test_pairs_each_leg_with_the_nodes_it_spans(self):
        builder = WalkSegmentBuilder(FakeGraph([100.0, 200.0], [0.0, 10.0, 5.0]))
        segments = builder.build([Leg(0, False), Leg(1, False)], [0, 1, 2])
        assert segments == [WalkSegment(100.0, 10.0), WalkSegment(200.0, -5.0)]

    def test_rejects_a_mismatched_node_count(self):
        """One more node than legs, always. An off-by-one here would silently
        corrupt every distance and rise downstream."""
        builder = WalkSegmentBuilder(FakeGraph([100.0], [0.0, 10.0]))
        with pytest.raises(ValueError, match="expected 2 nodes"):
            builder.build([Leg(0, False)], [0, 1, 2])


class TestSuitabilityRules:
    def test_grade_rule_is_silent_within_tolerance(self, lean_profile):
        effort = WalkEffort.evaluate(lean_profile, [(1000.0, 20.0)])
        penalty, reason = GradeToleranceRule().evaluate(lean_profile, effort)
        assert penalty == 0.0 and reason is None

    def test_grade_rule_fires_and_explains_itself(self, heavy_profile):
        effort = WalkEffort.evaluate(heavy_profile, [(500.0, 90.0)])
        penalty, reason = GradeToleranceRule().evaluate(heavy_profile, effort)
        assert penalty > 0
        assert "%" in reason

    def test_grade_penalty_is_capped(self, heavy_profile):
        effort = WalkEffort.evaluate(heavy_profile, [(100.0, 45.0)])
        penalty, _ = GradeToleranceRule().evaluate(heavy_profile, effort)
        assert penalty <= GradeToleranceRule.max_penalty

    def test_climb_rule_fires_on_relentless_terrain(self, lean_profile):
        effort = WalkEffort.evaluate(lean_profile, [(1000.0, 80.0)])
        penalty, reason = TotalClimbRule().evaluate(lean_profile, effort)
        assert penalty > 0 and "climb per km" in reason

    def test_duration_rule_respects_the_walker_s_limit(self, heavy_profile):
        long_walk = WalkEffort.evaluate(heavy_profile, [(6000.0, 0.0)])
        penalty, reason = DurationRule().evaluate(heavy_profile, long_walk)
        assert penalty > 0 and "long outing" in reason

    def test_intensity_rule_warns_without_penalising(self, older_profile):
        """A light walk is a good walk; it just cannot claim guideline credit."""
        effort = WalkEffort.evaluate(older_profile, [(200.0, 0.0)])
        penalty, reason = IntensityRule().evaluate(older_profile, effort)
        assert penalty == 0.0
        if not effort.counts_as_moderate:
            assert "moderate-intensity threshold" in reason


class TestSuitabilityAssessment:
    def test_score_is_bounded(self, heavy_profile):
        brutal = WalkEffort.evaluate(heavy_profile, [(300.0, 130.0), (300.0, -130.0)] * 12)
        assessment = SuitabilityAssessment(heavy_profile, brutal)
        assert 0 <= assessment.score <= 100

    def test_always_gives_at_least_one_reason(self, lean_profile):
        gentle = WalkEffort.evaluate(lean_profile, [(1500.0, 2.0)])
        assert SuitabilityAssessment(lean_profile, gentle).notes

    def test_serialises_score_and_notes_together(self, lean_profile):
        effort = WalkEffort.evaluate(lean_profile, [(1500.0, 2.0)])
        payload = SuitabilityAssessment(lean_profile, effort).to_dict()
        assert set(payload) == {"score", "notes"}
