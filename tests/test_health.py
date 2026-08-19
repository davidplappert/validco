"""Tests for the physiology model.

These are mostly *validation* tests rather than unit tests: they check the model
against independently published values (the Compendium of Physical Activities,
Minetti's own reported figures, Bohannon's gait-speed table). A refactor that
keeps the code working but breaks agreement with the literature is exactly the
regression worth catching, because nothing else in the system would notice.
"""

from __future__ import annotations

import math

import pytest
from stepwise.models.effort import WalkEffort
from stepwise.models.health import HealthReport
from stepwise.models.profile import Profile
from stepwise.models.suitability import SuitabilityAssessment
from stepwise.physiology.anthropometry import BodyComposition, UnitConverter
from stepwise.physiology.energy import MinettiCostModel
from stepwise.physiology.speed import ToblerSpeedModel

MINETTI = MinettiCostModel()
MINETTI_GRADE_LIMIT = MINETTI.grade_limit
TOBLER = ToblerSpeedModel()


def minetti_cost(grade):
    """Adapter keeping these validation tests readable."""
    return MINETTI.cost_j_per_kg_m(grade)


def tobler_speed_factor(grade):
    """Adapter keeping these validation tests readable."""
    return TOBLER.factor(grade)


def evaluate_walk(profile, segments, **kw):
    """Adapter keeping these validation tests readable."""
    return WalkEffort.evaluate(profile, segments, **kw)


def health_effects(profile, effort):
    """Adapter keeping these validation tests readable."""
    return HealthReport(profile, effort).to_dict()


def suitability(profile, effort):
    """Adapter keeping these validation tests readable."""
    return SuitabilityAssessment(profile, effort).to_dict()


bmi_class = BodyComposition.classify
lb_to_kg = UnitConverter.lb_to_kg
ft_in_to_cm = UnitConverter.ft_in_to_cm


def intensity_band(mets):
    """Intensity band for a MET value, mirroring WalkEffort.intensity."""
    if mets >= 6.0:
        return "vigorous"
    return "moderate" if mets >= 3.0 else "light"


class TestMinetti:
    def test_level_cost_matches_published_value(self):
        # Minetti et al. (2002): Cw on the level is 2.5 J/kg/m.
        assert minetti_cost(0.0) == pytest.approx(2.5, abs=0.01)

    def test_minimum_is_on_a_shallow_descent(self):
        """The curve's defining feature: a gentle descent is cheaper than flat.

        The published *minimum Cw* series (cost at each gradient's optimal
        speed) bottoms at -0.10. This polynomial regresses average cost across
        the tested speeds instead, and bottoms slightly lower, near -0.15 — so
        the assertion is on the band, not on a single point. What must hold in
        either reading is that a gentle descent costs materially less than the
        level, which is what stops the model treating a San Francisco loop's
        downhill leg as merely "not uphill".
        """
        grades = [i / 1000.0 for i in range(-450, 451)]
        cheapest = min(grades, key=minetti_cost)
        assert -0.20 <= cheapest <= -0.08, f"minimum at {cheapest}"
        assert minetti_cost(cheapest) < 0.5 * minetti_cost(0.0)

    def test_level_cost_is_the_average_speed_regression(self):
        """Guards the choice of curve: 2.5 J/kg/m is the average-cost fit at
        i=0, not the 1.64 J/kg/m speed-optimal minimum. Swapping one for the
        other would shift every calorie figure by ~35%."""
        assert minetti_cost(0.0) == pytest.approx(2.5, abs=0.01)

    def test_steep_climb_matches_published_value(self):
        # 17.33 J/kg/m at +45% in the paper.
        assert minetti_cost(0.45) == pytest.approx(17.33, rel=0.05)

    def test_steep_descent_costs_more_than_the_minimum(self):
        """Braking eccentrically on a steep descent is expensive again."""
        assert minetti_cost(-0.35) > minetti_cost(-0.10)

    def test_cost_is_never_free(self):
        for grade in (-0.9, -0.45, -0.2, 0.0, 0.2, 0.9):
            assert minetti_cost(grade) > 0.0

    def test_grades_are_clamped_to_the_measured_range(self):
        assert minetti_cost(5.0) == minetti_cost(MINETTI_GRADE_LIMIT)
        assert minetti_cost(-5.0) == minetti_cost(-MINETTI_GRADE_LIMIT)


class TestTobler:
    def test_flat_is_unity(self):
        """The factor is normalised so the walker's own flat speed sets scale."""
        assert tobler_speed_factor(0.0) == pytest.approx(1.0, abs=1e-9)

    def test_fastest_on_a_slight_downhill(self):
        grades = [i / 1000.0 for i in range(-300, 301)]
        fastest = max(grades, key=tobler_speed_factor)
        assert fastest == pytest.approx(-0.05, abs=0.005)

    def test_uphill_is_slower_than_downhill(self):
        assert tobler_speed_factor(0.10) < tobler_speed_factor(-0.10)


class TestProfile:
    def test_bmi_and_class(self):
        p = Profile(sex="male", age_years=33, weight_kg=lb_to_kg(320), height_cm=182.9)
        assert p.bmi == pytest.approx(43.4, abs=0.5)
        assert bmi_class(p.bmi) == "obesity class III"

    def test_height_defaults_are_flagged(self):
        """A guessed height must never be presented as a known one."""
        p = Profile(sex="female", age_years=40, weight_kg=70)
        assert p.height_assumed is True
        assert p.height_cm == pytest.approx(161.3)
        assert p.to_dict()["height_assumed"] is True

        q = Profile(sex="female", age_years=40, weight_kg=70, height_cm=170)
        assert q.height_assumed is False

    def test_mifflin_st_jeor(self):
        # Worked example: 80 kg, 180 cm, 30 y male -> 10*80 + 6.25*180 - 5*30 + 5
        p = Profile(sex="male", age_years=30, weight_kg=80, height_cm=180)
        assert p.rmr_kcal_day == pytest.approx(800 + 1125 - 150 + 5)

        q = Profile(sex="female", age_years=30, weight_kg=80, height_cm=180)
        assert q.rmr_kcal_day == pytest.approx(800 + 1125 - 150 - 161)

    def test_gait_speed_matches_bohannon(self):
        """Bohannon & Andrews (2011) comfortable gait speed, healthy BMI."""
        m30 = Profile(sex="male", age_years=30, weight_kg=75, height_cm=178)
        assert m30.baseline_speed_ms == pytest.approx(1.433, abs=0.02)
        f30 = Profile(sex="female", age_years=30, weight_kg=62, height_cm=165)
        assert f30.baseline_speed_ms == pytest.approx(1.337, abs=0.02)

    def test_speed_declines_with_age(self):
        young = Profile(sex="male", age_years=30, weight_kg=75, height_cm=178)
        old = Profile(sex="male", age_years=80, weight_kg=75, height_cm=178)
        assert old.baseline_speed_ms < young.baseline_speed_ms

    def test_speed_declines_with_obesity_class(self):
        speeds = [
            Profile(sex="male", age_years=33, weight_kg=w, height_cm=178).baseline_speed_ms
            for w in (72, 95, 115, 135, 165)  # healthy -> class III
        ]
        assert speeds == sorted(speeds, reverse=True), speeds

    def test_step_length_shortens_with_obesity(self):
        lean = Profile(sex="male", age_years=33, weight_kg=75, height_cm=180)
        heavy = Profile(sex="male", age_years=33, weight_kg=165, height_cm=180)
        assert heavy.step_length_m < lean.step_length_m - 0.05


class TestEvaluateWalk:
    def test_flat_walk_matches_compendium_met_value(self):
        """A ~2.5 mph flat walk is 3.0 METs in the Compendium of Physical
        Activities. Landing within half a MET of that is the single best
        end-to-end check that the energy model is not drifting."""
        p = Profile(sex="male", age_years=33, weight_kg=lb_to_kg(320), height_cm=175.3)
        effort = evaluate_walk(p, [(2000.0, 0.0)])
        assert effort.avg_speed_ms * 2.23694 == pytest.approx(2.57, abs=0.2)
        assert effort.mets == pytest.approx(3.0, abs=0.5)

    def test_rolling_terrain_costs_more_than_flat(self):
        """Net-zero elevation change is not the same as flat ground.

        This is why the model works edge by edge instead of from route totals.
        """
        p = Profile(sex="male", age_years=35, weight_kg=80, height_cm=178)
        flat = evaluate_walk(p, [(2000.0, 0.0)])
        rolling = evaluate_walk(p, [(1000.0, 60.0), (1000.0, -60.0)])
        assert rolling.kcal_gross > flat.kcal_gross
        assert rolling.duration_s > flat.duration_s
        assert rolling.ascent_m == pytest.approx(60.0)
        assert rolling.descent_m == pytest.approx(60.0)

    def test_ascent_and_descent_are_tracked_separately(self):
        p = Profile(sex="male", age_years=35, weight_kg=80, height_cm=178)
        effort = evaluate_walk(p, [(500.0, 30.0), (500.0, -10.0), (500.0, 5.0)])
        assert effort.ascent_m == pytest.approx(35.0)
        assert effort.descent_m == pytest.approx(10.0)

    def test_heavier_walker_burns_more(self):
        light = Profile(sex="male", age_years=33, weight_kg=70, height_cm=178)
        heavy = Profile(sex="male", age_years=33, weight_kg=140, height_cm=178)
        steps = [(2000.0, 20.0)]
        assert evaluate_walk(heavy, steps).kcal_gross > evaluate_walk(light, steps).kcal_gross

    def test_crossing_pauses_extend_duration(self):
        p = Profile(sex="male", age_years=33, weight_kg=80, height_cm=178)
        without = evaluate_walk(p, [(1000.0, 0.0)])
        with_pause = evaluate_walk(p, [(1000.0, 0.0)], pause_s=120.0)
        assert with_pause.duration_s == pytest.approx(without.duration_s + 120.0)
        # A pause adds resting calories but no movement calories.
        assert with_pause.kcal_net == pytest.approx(without.kcal_net)
        assert with_pause.kcal_gross > without.kcal_gross

    def test_empty_walk_is_rejected(self):
        p = Profile(sex="male", age_years=33, weight_kg=80, height_cm=178)
        with pytest.raises(ValueError):
            evaluate_walk(p, [])

    def test_dem_noise_cannot_produce_absurd_gradients(self):
        """A 1 m edge with a 40 m rise is DEM noise, not a cliff."""
        p = Profile(sex="male", age_years=33, weight_kg=80, height_cm=178)
        effort = evaluate_walk(p, [(1.0, 40.0)])
        assert math.isfinite(effort.kcal_gross)
        assert effort.peak_grade <= MINETTI_GRADE_LIMIT + 1e-9


class TestHealthEffects:
    def test_guideline_and_step_progress(self):
        p = Profile(sex="male", age_years=33, weight_kg=lb_to_kg(320), height_cm=182.9)
        effort = evaluate_walk(p, [(2500.0, 0.0)])
        effects = health_effects(p, effort)

        guideline = effects["guideline_progress"]
        assert guideline["who_weekly_moderate_min"] == 150
        assert 0 < guideline["pct_of_weekly_target"] <= 100

        steps = effects["steps"]
        assert steps["daily_target"] == 7000
        assert steps["walk_steps"] > 0

    def test_light_intensity_does_not_count_toward_the_weekly_target(self):
        """An 85-year-old strolling is below 3 METs; claiming otherwise would
        misrepresent the WHO guideline, which is explicitly moderate-intensity."""
        p = Profile(sex="female", age_years=85, weight_kg=55, height_cm=158)
        effort = evaluate_walk(p, [(500.0, 0.0)])
        effects = health_effects(p, effort)
        if effort.mets < 3.0:
            assert effects["guideline_progress"]["moderate_minutes"] == 0
            assert effects["guideline_progress"]["counts_as_moderate"] is False

    def test_assumed_height_is_disclosed_as_a_caveat(self):
        p = Profile(sex="male", age_years=33, weight_kg=90)
        effects = health_effects(p, evaluate_walk(p, [(1000.0, 0.0)]))
        assert any("Height was assumed" in c for c in effects["caveats"])

    def test_joint_load_scales_with_body_weight(self):
        light = Profile(sex="male", age_years=33, weight_kg=70, height_cm=178)
        heavy = Profile(sex="male", age_years=33, weight_kg=160, height_cm=178)
        steps = [(1000.0, 0.0)]
        a = health_effects(light, evaluate_walk(light, steps))["joint_load"]
        b = health_effects(heavy, evaluate_walk(heavy, steps))["joint_load"]
        assert b["peak_knee_force_lb"] > a["peak_knee_force_lb"]

    def test_intensity_bands(self):
        assert intensity_band(2.0) == "light"
        assert intensity_band(3.0) == "moderate"
        assert intensity_band(6.5) == "vigorous"


class TestSuitability:
    def test_steep_route_scores_worse_for_a_high_bmi_walker(self):
        heavy = Profile(sex="male", age_years=33, weight_kg=lb_to_kg(320), height_cm=182.9)
        gentle = evaluate_walk(heavy, [(2000.0, 5.0)])
        brutal = evaluate_walk(heavy, [(600.0, 90.0), (600.0, -90.0)])
        assert suitability(heavy, brutal)["score"] < suitability(heavy, gentle)["score"]

    def test_grade_tolerance_is_tighter_at_higher_bmi(self):
        lean = Profile(sex="male", age_years=33, weight_kg=75, height_cm=180)
        heavy = Profile(sex="male", age_years=33, weight_kg=165, height_cm=180)
        hill = [(800.0, 64.0), (800.0, -64.0)]  # 8% grade
        assert (
            suitability(heavy, evaluate_walk(heavy, hill))["score"]
            < suitability(lean, evaluate_walk(lean, hill))["score"]
        )

    def test_a_comfortable_route_always_explains_itself(self):
        p = Profile(sex="male", age_years=33, weight_kg=80, height_cm=180)
        result = suitability(p, evaluate_walk(p, [(1500.0, 3.0)]))
        assert result["notes"], "suitability must always give a reason"
        assert 0 <= result["score"] <= 100


def test_unit_conversions():
    assert lb_to_kg(320) == pytest.approx(145.15, abs=0.01)
    assert ft_in_to_cm(6, 0) == pytest.approx(182.88, abs=0.01)
    assert ft_in_to_cm(5, 10) == pytest.approx(177.8, abs=0.01)


class TestWeightProjection:
    """The most-requested number in a fitness app, and the easiest to overstate.

    These tests pin the honesty properties rather than exact figures: that the
    long-run projection is materially *smaller* than the naive rule, that it
    uses net rather than gross calories, and that it refuses to project from a
    deficit too small to mean anything.
    """

    def _projection(self, sessions=3):
        from stepwise.models.health import WeightProjection

        profile = Profile("male", 45, lb_to_kg(320), 182.88)
        effort = evaluate_walk(profile, [(2100.0, 4.0)])
        return WeightProjection(profile, effort), effort, profile

    def test_one_year_is_far_below_the_naive_3500_rule(self):
        """The whole point of using Hall's model.

        Applied for a year the static rule roughly doubles real loss, because
        it ignores the fall in metabolism as body mass drops. If this ever
        stops holding, the app has quietly reverted to the wrong model.
        """
        projection, effort, _ = self._projection()
        result = projection.for_frequency(3)

        naive_one_year_lb = (effort.kcal_net * 3 * 52) / 3500.0
        assert result["one_year_lb"] < naive_one_year_lb * 0.75

    def test_projections_scale_with_frequency(self):
        projection, _, _ = self._projection()
        values = [projection.for_frequency(f)["eventual_lb"] for f in (3, 5, 7)]
        assert values == sorted(values)

    def test_uses_net_calories_not_gross(self):
        """Only energy above resting can contribute to a deficit. Using gross
        — as most calculators do — inflates every projection."""
        projection, effort, _ = self._projection()
        weekly = projection.for_frequency(3)["weekly_kcal"]
        assert weekly == pytest.approx(effort.kcal_net * 3, abs=1)
        assert weekly < effort.kcal_gross * 3

    def test_settles_above_the_one_year_figure(self):
        """Weight change continues past a year, then plateaus."""
        projection, _, _ = self._projection()
        result = projection.for_frequency(5)
        assert result["eventual_lb"] > result["one_year_lb"]
        assert result["one_year_lb"] > result["first_month_lb"]

    def test_a_trivial_walk_is_not_projected(self):
        """Below a real weekly deficit, a number would imply precision that is
        not there — so the model says so instead."""
        from stepwise.models.health import WeightProjection

        profile = Profile("female", 30, 60.0, 165.0)
        tiny = evaluate_walk(profile, [(120.0, 0.0)])
        assert WeightProjection(profile, tiny).for_frequency(3)["meaningful"] is False

    def test_appetite_compensation_is_disclosed(self):
        """The single biggest reason real loss undershoots these numbers."""
        projection, _, _ = self._projection()
        caveats = " ".join(projection.to_dict()["caveats"]).lower()
        assert "eat" in caveats

    def test_reports_the_share_of_body_weight(self):
        projection, _, profile = self._projection()
        result = projection.for_frequency(7)
        assert 0 < result["eventual_pct_of_body_weight"] < 100

    def test_appears_in_the_health_report(self):
        profile = Profile("male", 45, lb_to_kg(320), 182.88)
        effort = evaluate_walk(profile, [(2100.0, 4.0)])
        payload = health_effects(profile, effort)
        assert "weight_projection" in payload
        assert payload["weight_projection"]["projections"]
