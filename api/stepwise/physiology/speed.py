"""How fast a given person walks, on the flat and on a gradient.

Two separable questions, so two classes:

* :class:`GaitSpeedModel` answers "how fast does someone of this age, sex and
  body mass walk on level ground", from population norms.
* :class:`GradientSpeedModel` answers "how does a slope scale that", as a
  dimensionless multiplier.

Keeping them apart is what lets the app use Tobler's hiking function for the
*shape* of the slope response without inheriting its assumption that the walker
manages 5 km/h on the flat — which a 33-year-old at 320 lb does not.

Sources
-------
Bohannon RW, Andrews AW. "Normal walking speed: a descriptive meta-analysis."
Physiotherapy 97(3):182-189, 2011.

Tobler W. "Three presentations on geographical analysis and modeling", 1993.

Ling W et al. "Gait and Function in Class III Obesity." J Obesity, 2012;
Browning RC, Kram R, on preferred speed and step length in obesity.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod

LOG = logging.getLogger(__name__)


class GradientSpeedModel(ABC):
    """Strategy interface: gradient in, speed multiplier out."""

    @abstractmethod
    def factor(self, grade: float) -> float:
        """Multiplier on flat-ground speed for a gradient. Flat must give 1.0."""


class ToblerSpeedModel(GradientSpeedModel):
    """Tobler's hiking function, normalised so level ground is 1.0.

    The original is ``W = 6 * exp(-3.5 * |i + 0.05|)`` km/h. Its absolute values
    describe a fit hiker, so only its shape is kept and the walker's own
    predicted flat speed supplies the scale. That composition is what makes the
    age, sex and body-mass adjustments meaningful on hills as well as flat.

    The ``+0.05`` offset encodes a real and slightly counter-intuitive finding:
    people walk fastest on a gentle *downhill*, around -2.9 degrees, not on the
    level.
    """

    DECAY = 3.5
    OPTIMAL_GRADE = -0.05

    def factor(self, grade: float) -> float:
        """Speed multiplier at ``grade``, equal to 1.0 when ``grade`` is zero.

        Normalisation divides out the function's value on the flat, so the
        exponent is written directly as the difference of absolute offsets.
        """
        shifted = abs(grade - self.OPTIMAL_GRADE)
        flat = abs(-self.OPTIMAL_GRADE)
        return math.exp(-self.DECAY * (shifted - flat))


class GaitSpeedModel:
    """Comfortable walking speed on level ground, from population norms.

    Two effects compose: the Bohannon & Andrews meta-analysis supplies speed by
    age and sex, and a body-mass term scales it down. The second is not a
    judgement about effort — reduced preferred speed in obesity is a documented
    adaptation that lowers joint loading and metabolic cost, and modelling it is
    what stops the app promising a 320 lb walker a pace they will not hold.
    """

    #: Comfortable gait speed in m/s, keyed by sex then by decade start.
    NORMS: dict[str, dict[int, float]] = {
        "male": {20: 1.358, 30: 1.433, 40: 1.434, 50: 1.433, 60: 1.339, 70: 1.262, 80: 0.968},
        "female": {20: 1.341, 30: 1.337, 40: 1.390, 50: 1.313, 60: 1.241, 70: 1.132, 80: 0.943},
    }

    #: Slowest speed the model will predict, m/s. Below this the person is not
    #: really walking and the whole model stops applying.
    FLOOR_MS = 0.4

    def __init__(self, gradient_model: GradientSpeedModel | None = None):
        """Accept a gradient strategy so tests can hold terrain constant."""
        self.gradient_model = gradient_model or ToblerSpeedModel()

    def baseline_ms(self, sex: str, age_years: int, bmi: float) -> float:
        """Comfortable level-ground speed in m/s for one person."""
        speed = self._norm_for_age(sex, age_years) * self.body_mass_factor(bmi)
        return max(self.FLOOR_MS, speed)

    def _norm_for_age(self, sex: str, age_years: int) -> float:
        """Look up the age/sex norm, interpolating between published decades.

        Interpolation matters for continuity: without it a 39-year-old and a
        40-year-old would get discontinuously different answers from what is
        really a smooth decline.
        """
        table = self.NORMS[sex]
        decade = max(20, min(80, (age_years // 10) * 10))
        speed = table[decade]
        nxt = decade + 10
        if nxt in table:
            fraction = (age_years - decade) / 10.0
            speed += (table[nxt] - speed) * fraction
        return speed

    def body_mass_factor(self, bmi: float) -> float:
        """Multiplier on comfortable speed for body mass.

        Anchored on reported preferred-speed differences by obesity class:
        negligible below BMI 30, about -3% across the overweight band, -6% at
        class I, -12% at class II, and -18% or more at class III, where gait
        changes qualitatively rather than just quantitatively. Floored at 0.72
        so extreme BMI stays physically sensible instead of trending to zero.
        """
        if bmi < 25.0:
            return 1.0
        if bmi < 30.0:
            return 1.0 - 0.03 * (bmi - 25.0) / 5.0
        if bmi < 35.0:
            return 0.970 - 0.030 * (bmi - 30.0) / 5.0
        if bmi < 40.0:
            return 0.940 - 0.060 * (bmi - 35.0) / 5.0
        return max(0.72, 0.880 - 0.030 * (bmi - 40.0) / 5.0)

    def speed_on_grade_ms(self, baseline_ms: float, grade: float) -> float:
        """Apply the gradient multiplier to a person's flat speed.

        Floored at 0.25 m/s: on a 45% climb the multiplier alone would predict a
        speed slow enough to make route durations diverge.
        """
        return max(0.25, baseline_ms * self.gradient_model.factor(grade))


class StepLengthModel:
    """Step length, used to turn a distance into a step count.

    Step count is the headline the step-mortality literature is expressed in, so
    it needs to be more than distance divided by a constant.
    """

    #: Step length as a fraction of stature.
    STATURE_RATIO = {"male": 0.415, "female": 0.413}

    #: Reduction in metres by obesity class — part of the same stability and
    #: economy adaptation that lowers preferred speed.
    OBESITY_REDUCTION_M = ((40.0, 0.09), (35.0, 0.07), (30.0, 0.05))

    #: Shortest step the model will predict, metres.
    FLOOR_M = 0.35

    #: Steep ground in either direction shortens the stride by roughly this much.
    STEEP_GRADE = 0.08
    STEEP_FACTOR = 0.85

    def length_m(self, sex: str, height_cm: float, bmi: float) -> float:
        """Level-ground step length for one person, in metres."""
        base = (height_cm / 100.0) * self.STATURE_RATIO[sex]
        for threshold, reduction in self.OBESITY_REDUCTION_M:
            if bmi >= threshold:
                base -= reduction
                break
        return max(self.FLOOR_M, base)

    def length_on_grade_m(self, level_length_m: float, grade: float) -> float:
        """Shorten the step on steep ground, uphill or down."""
        return level_length_m * (self.STEEP_FACTOR if abs(grade) > self.STEEP_GRADE else 1.0)
