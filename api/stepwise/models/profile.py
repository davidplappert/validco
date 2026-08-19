"""The walker.

:class:`Profile` is the heaviest model in the app and deliberately so: it owns
every derived physiological quantity, so no caller ever recomputes a BMI or
guesses a walking speed. Controllers construct one from request JSON and then
only ask it questions.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from ..physiology.anthropometry import BodyComposition, RestingMetabolism, UnitConverter
from ..physiology.speed import GaitSpeedModel, StepLengthModel

LOG = logging.getLogger(__name__)

Sex = Literal["male", "female"]

#: Mean adult stature from NHANES 2015-2018 (US, 20+), used only when the user
#: does not supply a height. It feeds resting metabolism and step length, both
#: of which are materially better with a real number.
DEFAULT_HEIGHT_CM: dict[str, float] = {"male": 175.3, "female": 161.3}

#: Accepted spellings for each sex on the way in.
_SEX_ALIASES = {
    "male": "male",
    "m": "male",
    "man": "male",
    "female": "female",
    "f": "female",
    "woman": "female",
}


class Profile:
    """One person's physiology, and everything derivable from it.

    Sex, age and weight are required — they are what the product asks for.
    Height is optional and defaults to a population mean, with
    :attr:`height_assumed` set so the assumption can be disclosed rather than
    silently folded into the numbers.

    Derived values are computed once in ``__init__`` and exposed as attributes.
    They are read constantly during route scoring (once per candidate route, and
    the speed once per graph edge in the search), so recomputing them behind
    properties would be measurable.
    """

    __slots__ = (
        "sex",
        "age_years",
        "weight_kg",
        "height_cm",
        "height_assumed",
        "bmi",
        "bmi_class",
        "body_fat_pct",
        "fat_free_mass_kg",
        "rmr_kcal_day",
        "rmr_kcal_min",
        "baseline_speed_ms",
        "step_length_m",
        "_gait",
        "_steps",
    )

    def __init__(
        self,
        sex: Sex,
        age_years: int,
        weight_kg: float,
        height_cm: float | None = None,
        *,
        composition: BodyComposition | None = None,
        metabolism: RestingMetabolism | None = None,
        gait: GaitSpeedModel | None = None,
        step_length: StepLengthModel | None = None,
    ):
        """Build a profile and derive everything that depends on it.

        The four physiology models are injectable so a test can hold one
        constant while varying another, but every call site in the app relies on
        the defaults.
        """
        composition = composition or BodyComposition()
        metabolism = metabolism or RestingMetabolism()
        self._gait = gait or GaitSpeedModel()
        self._steps = step_length or StepLengthModel()

        self.sex: Sex = sex
        self.age_years = age_years
        self.weight_kg = weight_kg
        self.height_assumed = height_cm is None
        self.height_cm = height_cm if height_cm is not None else DEFAULT_HEIGHT_CM[sex]

        self.bmi = composition.bmi(self.weight_kg, self.height_cm)
        self.bmi_class = composition.classify(self.bmi)
        self.body_fat_pct = composition.body_fat_pct(self.bmi, age_years, sex)
        self.fat_free_mass_kg = composition.fat_free_mass_kg(self.weight_kg, self.body_fat_pct)

        self.rmr_kcal_day = metabolism.kcal_per_day(self.weight_kg, self.height_cm, age_years, sex)
        self.rmr_kcal_min = self.rmr_kcal_day / 1440.0

        self.baseline_speed_ms = self._gait.baseline_ms(sex, age_years, self.bmi)
        self.step_length_m = self._steps.length_m(sex, self.height_cm, self.bmi)

        LOG.debug(
            "profile built sex=%s age=%d kg=%.1f bmi=%.1f speed=%.2fm/s rmr=%.0f",
            sex,
            age_years,
            self.weight_kg,
            self.bmi,
            self.baseline_speed_ms,
            self.rmr_kcal_day,
        )

    # --- construction -----------------------------------------------------

    @classmethod
    def normalise_sex(cls, raw: Any) -> Sex | None:
        """Fold an incoming sex value to ``male``/``female``, or ``None``.

        Returning ``None`` rather than raising keeps the validation error and
        its explanation in the controller, where the HTTP status lives.
        """
        return _SEX_ALIASES.get(str(raw or "").strip().lower())  # type: ignore[return-value]

    @classmethod
    def from_imperial(
        cls,
        sex: Sex,
        age_years: int,
        weight_lb: float,
        height_ft: float | None = None,
        height_in: float = 0.0,
    ) -> Profile:
        """Build from pounds, feet and inches.

        Exists so unit conversion happens once, at the edge, and the rest of the
        model stays unambiguously metric.
        """
        height_cm = (
            UnitConverter.ft_in_to_cm(height_ft, height_in) if height_ft is not None else None
        )
        return cls(sex, age_years, UnitConverter.lb_to_kg(weight_lb), height_cm)

    # --- queries ----------------------------------------------------------

    def speed_on_grade_ms(self, grade: float) -> float:
        """This walker's speed on a given gradient, in m/s."""
        return self._gait.speed_on_grade_ms(self.baseline_speed_ms, grade)

    def steps_for(self, distance_m: float, grade: float) -> int:
        """Number of steps to cover a distance at a gradient."""
        length = self._steps.length_on_grade_m(self.step_length_m, grade)
        return int(distance_m / length) if length > 0 else 0

    def resting_kcal(self, duration_s: float) -> float:
        """Calories this walker would burn at rest over a span of time.

        Subtracted out to report a walk's *net* cost, and added in to report its
        gross cost — the distinction most fitness apps leave ambiguous.
        """
        return self.rmr_kcal_min * (duration_s / 60.0)

    @property
    def is_high_bmi(self) -> bool:
        """Whether joint-loading and gradient advice should be more cautious."""
        return self.bmi >= 40.0

    def grade_tolerance(self) -> float:
        """The steepest gradient that still counts as comfortable for this walker.

        Tightens with BMI: the same 10% hill that a lean walker barely registers
        is a genuine obstacle at class III obesity, and recommending it anyway
        would make the app's suggestions untrustworthy.
        """
        if self.bmi < 30.0:
            return 0.10
        return 0.07 if self.bmi < 40.0 else 0.05

    def comfortable_duration_min(self) -> float:
        """A sensible upper bound on a single outing for this walker."""
        if self.bmi < 30.0:
            return 60.0
        return 45.0 if self.bmi < 40.0 else 30.0

    # --- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response.

        ``height_assumed`` is included deliberately: the frontend surfaces it as
        a caveat, and dropping it would present a guessed height as a known one.
        """
        return {
            "sex": self.sex,
            "age_years": self.age_years,
            "weight_kg": round(self.weight_kg, 1),
            "weight_lb": round(UnitConverter.kg_to_lb(self.weight_kg)),
            "height_cm": round(self.height_cm, 1),
            "height_assumed": self.height_assumed,
            "bmi": round(self.bmi, 1),
            "bmi_class": self.bmi_class,
            "body_fat_pct_est": round(self.body_fat_pct, 1),
            "fat_free_mass_kg": round(self.fat_free_mass_kg, 1),
            "rmr_kcal_day": round(self.rmr_kcal_day),
            "baseline_speed_ms": round(self.baseline_speed_ms, 3),
            "baseline_speed_mph": round(UnitConverter.ms_to_mph(self.baseline_speed_ms), 2),
            "step_length_m": round(self.step_length_m, 3),
        }

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return (
            f"Profile(sex={self.sex!r}, age={self.age_years}, "
            f"kg={self.weight_kg:.1f}, bmi={self.bmi:.1f})"
        )
