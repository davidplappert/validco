"""Body composition and resting metabolism.

Small classes, but they carry the single most consequential modelling decision
in the project. The usual way to estimate the calories of a walk is the ACSM
equation, whose resting term is a flat 3.5 mL/kg/min of *total* body mass. That
scales resting metabolism linearly with weight, and adipose tissue is far less
metabolically active than muscle, so the equation is documented to overestimate
energy expenditure in people with obesity — the users this app is most useful
to.

Splitting resting metabolism out and estimating it with Mifflin-St Jeor, which
is validated across BMI classes and scales sub-linearly with mass, is what fixes
that. The movement term legitimately scales with total mass, because gravity
acts on all of it.

Sources
-------
Mifflin MD, St Jeor ST, Hill LA, Scott BJ, Daugherty SA, Koh YO. "A new
predictive equation for resting energy expenditure in healthy individuals."
Am J Clin Nutr 51:241-247, 1990.

Deurenberg P, Weststrate JA, Seidell JC. "Body mass index as a measure of body
fatness: age- and sex-specific prediction formulas." Br J Nutr 65:105-114, 1991.
"""

from __future__ import annotations

import logging

LOG = logging.getLogger(__name__)


class UnitConverter:
    """US customary to metric, kept in one place so no call site improvises."""

    KG_PER_LB = 0.45359237
    CM_PER_INCH = 2.54
    M_PER_MILE = 1609.344
    FT_PER_M = 3.28084
    MPH_PER_MS = 2.23694

    @classmethod
    def lb_to_kg(cls, pounds: float) -> float:
        """Pounds to kilograms."""
        return pounds * cls.KG_PER_LB

    @classmethod
    def kg_to_lb(cls, kilograms: float) -> float:
        """Kilograms to pounds."""
        return kilograms / cls.KG_PER_LB

    @classmethod
    def ft_in_to_cm(cls, feet: float, inches: float = 0.0) -> float:
        """Feet and inches to centimetres."""
        return (feet * 12.0 + inches) * cls.CM_PER_INCH

    @classmethod
    def m_to_miles(cls, metres: float) -> float:
        """Metres to miles."""
        return metres / cls.M_PER_MILE

    @classmethod
    def m_to_ft(cls, metres: float) -> float:
        """Metres to feet."""
        return metres * cls.FT_PER_M

    @classmethod
    def ms_to_mph(cls, metres_per_second: float) -> float:
        """Metres per second to miles per hour."""
        return metres_per_second * cls.MPH_PER_MS


class BodyComposition:
    """Body-fat and fat-free mass estimates from BMI, age and sex.

    Population-level only: Deurenberg's standard error is around four percentage
    points for an individual. It is used here to *explain* why the resting-
    metabolism treatment differs from the textbook one, never as a headline
    number shown on its own.
    """

    #: Clamp the estimate to a physiologically possible band; the linear formula
    #: runs off the end at extreme BMI.
    BOUNDS_PCT = (3.0, 70.0)

    def body_fat_pct(self, bmi: float, age_years: int, sex: str) -> float:
        """Estimated body fat as a percentage of total mass (Deurenberg 1991)."""
        sex_term = 1.0 if sex == "male" else 0.0
        pct = 1.20 * bmi + 0.23 * age_years - 10.8 * sex_term - 5.4
        low, high = self.BOUNDS_PCT
        return max(low, min(high, pct))

    def fat_free_mass_kg(self, weight_kg: float, body_fat_pct: float) -> float:
        """Lean mass in kilograms, given total mass and fat percentage."""
        return weight_kg * (1.0 - body_fat_pct / 100.0)

    @staticmethod
    def bmi(weight_kg: float, height_cm: float) -> float:
        """Body mass index in kg/m^2."""
        height_m = height_cm / 100.0
        return weight_kg / (height_m * height_m)

    @staticmethod
    def classify(bmi: float) -> str:
        """WHO BMI category as a plain-language string."""
        if bmi < 18.5:
            return "underweight"
        if bmi < 25.0:
            return "healthy"
        if bmi < 30.0:
            return "overweight"
        if bmi < 35.0:
            return "obesity class I"
        if bmi < 40.0:
            return "obesity class II"
        return "obesity class III"


class RestingMetabolism:
    """Resting energy expenditure, Mifflin-St Jeor.

    ``RMR = 10*kg + 6.25*cm - 5*age + s``, where ``s`` is +5 for men and -161
    for women. Chosen over ACSM's flat 3.5 mL/kg/min for the reason in this
    module's docstring.
    """

    SEX_CONSTANT = {"male": 5.0, "female": -161.0}

    def kcal_per_day(self, weight_kg: float, height_cm: float, age_years: int, sex: str) -> float:
        """Resting energy expenditure in kcal per 24 hours."""
        base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years
        return base + self.SEX_CONSTANT[sex]

    def kcal_per_minute(
        self, weight_kg: float, height_cm: float, age_years: int, sex: str
    ) -> float:
        """Resting energy expenditure in kcal per minute."""
        return self.kcal_per_day(weight_kg, height_cm, age_years, sex) / 1440.0
