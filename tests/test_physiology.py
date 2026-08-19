"""Tests for the physiology strategies.

These sit below the models and are the layer where a coefficient error would be
invisible everywhere else — a wrong number here just produces plausible-looking
calories.
"""

from __future__ import annotations

import pytest
from stepwise.physiology.anthropometry import BodyComposition, RestingMetabolism, UnitConverter
from stepwise.physiology.energy import AcsmCostModel, MinettiCostModel
from stepwise.physiology.speed import (
    GaitSpeedModel,
    StepLengthModel,
    ToblerSpeedModel,
)


class TestMinettiVsAcsm:
    """The comparison that justifies the whole modelling choice."""

    def test_acsm_goes_nonsensical_downhill(self):
        """ACSM's vertical term is linear and unbounded below.

        This is the concrete reason the app does not use it: on San Francisco's
        gradients it would predict the downhill leg refunding the uphill leg.
        Asserting the failure keeps the rationale honest rather than folklore.
        """
        acsm = AcsmCostModel(speed_m_per_min=80.0)
        raw_vo2 = acsm.vo2_ml_per_kg_min(-0.30)
        assert raw_vo2 < 3.5, "ACSM predicts sub-resting VO2 on a 30% descent"

    def test_minetti_stays_positive_everywhere(self):
        minetti = MinettiCostModel()
        for grade in (-0.45, -0.30, -0.15, 0.0, 0.15, 0.45):
            assert minetti.cost_j_per_kg_m(grade) > 0.0

    def test_both_agree_within_reason_on_the_flat(self):
        """A sanity check that the two models are measuring the same thing."""
        minetti = MinettiCostModel().cost_j_per_kg_m(0.0)
        acsm = AcsmCostModel(speed_m_per_min=80.0).cost_j_per_kg_m(0.0)
        assert acsm == pytest.approx(minetti, rel=0.35)


class TestRelativeCost:
    def test_flat_is_unity(self):
        assert MinettiCostModel().relative_to_flat(0.0) == pytest.approx(1.0)

    def test_climbing_costs_more_than_flat(self):
        assert MinettiCostModel().relative_to_flat(0.15) > 1.5

    def test_gentle_descent_costs_less_than_flat(self):
        assert MinettiCostModel().relative_to_flat(-0.12) < 1.0


class TestClamping:
    def test_dem_noise_cannot_escape_the_fitted_range(self):
        """A 1 m edge with a 40 m rise is DEM noise, not a cliff."""
        model = MinettiCostModel()
        assert model.clamp(40.0) == model.grade_limit
        assert model.clamp(-40.0) == -model.grade_limit
        assert model.cost_j_per_kg_m(40.0) == model.cost_j_per_kg_m(model.grade_limit)


class TestGaitSpeed:
    def test_interpolates_between_published_decades(self):
        """A 35-year-old must sit between the 30s and 40s norms, not jump."""
        model = GaitSpeedModel()
        at30 = model.baseline_ms("male", 30, 22.0)
        at35 = model.baseline_ms("male", 35, 22.0)
        at40 = model.baseline_ms("male", 40, 22.0)
        assert min(at30, at40) <= at35 <= max(at30, at40)

    def test_body_mass_factor_is_monotonic(self):
        model = GaitSpeedModel()
        factors = [model.body_mass_factor(bmi) for bmi in (22, 27, 32, 37, 45, 55)]
        assert factors == sorted(factors, reverse=True)

    def test_body_mass_factor_is_floored(self):
        """Extreme BMI must not trend the predicted speed toward zero."""
        assert GaitSpeedModel().body_mass_factor(90.0) >= 0.72

    def test_healthy_bmi_is_unpenalised(self):
        assert GaitSpeedModel().body_mass_factor(22.0) == 1.0

    def test_speed_never_falls_below_the_floor(self):
        assert GaitSpeedModel().baseline_ms("female", 95, 60.0) >= 0.4

    def test_gradient_slows_the_walker(self):
        model = GaitSpeedModel()
        flat = model.speed_on_grade_ms(1.4, 0.0)
        uphill = model.speed_on_grade_ms(1.4, 0.15)
        assert uphill < flat

    def test_speed_on_steep_ground_is_floored(self):
        assert GaitSpeedModel().speed_on_grade_ms(1.4, 0.45) >= 0.25


class TestTobler:
    def test_normalised_to_flat(self):
        assert ToblerSpeedModel().factor(0.0) == pytest.approx(1.0, abs=1e-9)

    def test_peak_is_on_a_gentle_downhill(self):
        model = ToblerSpeedModel()
        assert model.factor(-0.05) > model.factor(0.0)
        assert model.factor(-0.05) > model.factor(-0.20)


class TestStepLength:
    def test_scales_with_stature(self):
        model = StepLengthModel()
        short = model.length_m("male", 160.0, 22.0)
        tall = model.length_m("male", 195.0, 22.0)
        assert tall > short

    def test_shortens_by_obesity_class(self):
        model = StepLengthModel()
        lengths = [model.length_m("male", 180.0, bmi) for bmi in (22, 32, 37, 45)]
        assert lengths == sorted(lengths, reverse=True)

    def test_never_below_the_floor(self):
        assert StepLengthModel().length_m("female", 130.0, 60.0) >= 0.35

    def test_steep_ground_shortens_the_stride(self):
        model = StepLengthModel()
        assert model.length_on_grade_m(0.7, 0.15) < model.length_on_grade_m(0.7, 0.01)


class TestAnthropometry:
    def test_bmi(self):
        assert BodyComposition.bmi(80.0, 180.0) == pytest.approx(24.7, abs=0.1)

    @pytest.mark.parametrize(
        "bmi,expected",
        [
            (17, "underweight"),
            (22, "healthy"),
            (27, "overweight"),
            (32, "obesity class I"),
            (37, "obesity class II"),
            (45, "obesity class III"),
        ],
    )
    def test_classification(self, bmi, expected):
        assert BodyComposition.classify(bmi) == expected

    def test_body_fat_is_bounded(self):
        composition = BodyComposition()
        assert 3.0 <= composition.body_fat_pct(90.0, 60, "male") <= 70.0
        assert 3.0 <= composition.body_fat_pct(12.0, 20, "female") <= 70.0

    def test_men_are_estimated_leaner_at_equal_bmi(self):
        composition = BodyComposition()
        assert composition.body_fat_pct(30.0, 40, "male") < composition.body_fat_pct(
            30.0, 40, "female"
        )

    def test_fat_free_mass(self):
        composition = BodyComposition()
        assert composition.fat_free_mass_kg(100.0, 40.0) == pytest.approx(60.0)

    def test_rmr_scales_sublinearly_with_mass(self):
        """The property that fixes ACSM's overestimate at high BMI.

        Doubling body mass must not double resting metabolism; Mifflin-St Jeor's
        10 kcal/kg term plus fixed height and age terms is what achieves that.
        """
        rmr = RestingMetabolism()
        light = rmr.kcal_per_day(80.0, 178.0, 33, "male")
        heavy = rmr.kcal_per_day(160.0, 178.0, 33, "male")
        assert heavy < 2 * light


class TestUnitConverter:
    def test_round_trips(self):
        assert UnitConverter.kg_to_lb(UnitConverter.lb_to_kg(361)) == pytest.approx(361)

    def test_known_values(self):
        assert UnitConverter.lb_to_kg(361) == pytest.approx(163.75, abs=0.01)
        assert UnitConverter.ft_in_to_cm(6, 0) == pytest.approx(182.88, abs=0.01)
        assert UnitConverter.m_to_miles(1609.344) == pytest.approx(1.0)
        assert UnitConverter.ms_to_mph(1.0) == pytest.approx(2.23694, abs=1e-4)
