"""Translating a walk into health outcomes the evidence actually supports.

Each guideline gets its own small model so the claim, its threshold and its
source sit together. The alternative — one function assembling a nested dict —
made it far too easy to state a number without its provenance, which for a
health app is the failure mode that matters.

Nothing here invents claims about weight loss or life expectancy from a single
walk. Every output is framed as "this walk is N% of a published guideline", plus
the caveats that guideline comes with.

Sources
-------
WHO. "Guidelines on physical activity and sedentary behaviour", 2020 —
150-300 min/week of moderate-intensity aerobic activity for adults 18-64.

Ding D et al. "Daily steps and health outcomes in adults: a systematic review
and dose-response meta-analysis." Lancet Public Health, 2025 — benefits inflect
around 7,000 steps/day; versus 2,000 steps/day, 7,000 tracks with 47% lower
all-cause mortality and 25% lower cardiovascular disease risk.

Messier SP et al. Arthritis Rheum 52:2026-2032, 2005 — roughly four pounds of
knee load per pound of body weight, per step.

Hall KD, Sacks G, Chandramohan D, Chow CC, Wang YC, Gortmaker SL, Swinburn BA.
"Quantification of the effect of energy imbalance on bodyweight." Lancet
378:826-837, 2011 — the dynamic model that replaces the 3,500 kcal-per-pound
rule, and the rule of thumb this app uses for long-run projections.
"""

from __future__ import annotations

import logging
from typing import Any

from ..physiology.anthropometry import UnitConverter

LOG = logging.getLogger(__name__)


class GuidelineProgress:
    """Progress toward the WHO weekly moderate-activity target."""

    WEEKLY_TARGET_MIN = 150
    WEEKLY_UPPER_MIN = 300

    def __init__(self, effort):
        """Derive weekly-target progress from one walk's effort."""
        self.minutes = effort.duration_min
        # Only moderate-intensity time counts. A stroll below 3 METs is good for
        # you and counts toward steps, but claiming it against a guideline that
        # says "moderate-intensity" would misrepresent the guideline.
        self.moderate_minutes = self.minutes if effort.counts_as_moderate else 0.0
        self.met_minutes = effort.mets * self.minutes
        self.counts_as_moderate = effort.counts_as_moderate

    @property
    def pct_of_weekly_target(self) -> float:
        """This walk as a percentage of 150 weekly moderate minutes."""
        return 100.0 * self.moderate_minutes / self.WEEKLY_TARGET_MIN

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "who_weekly_moderate_min": self.WEEKLY_TARGET_MIN,
            "who_weekly_upper_min": self.WEEKLY_UPPER_MIN,
            "moderate_minutes": round(self.moderate_minutes, 1),
            "pct_of_weekly_target": round(self.pct_of_weekly_target, 1),
            "met_minutes": round(self.met_minutes),
            "counts_as_moderate": self.counts_as_moderate,
        }


class StepProgress:
    """Progress toward the daily step target."""

    DAILY_TARGET = 7000

    CONTEXT = (
        "7,000 steps/day is where most outcomes plateau; versus 2,000 steps/day "
        "it tracks with 47% lower all-cause mortality and 25% lower "
        "cardiovascular disease risk."
    )

    def __init__(self, effort):
        """Derive step-target progress from one walk's effort."""
        self.walk_steps = effort.steps

    @property
    def pct_of_daily_target(self) -> float:
        """This walk as a percentage of a 7,000-step day."""
        return 100.0 * self.walk_steps / self.DAILY_TARGET

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "walk_steps": self.walk_steps,
            "daily_target": self.DAILY_TARGET,
            "pct_of_daily_target": round(self.pct_of_daily_target, 1),
            "context": self.CONTEXT,
        }


class EnergyReport:
    """Calories, with the gross/net distinction made explicit.

    Most apps report one number without saying which. The difference is not
    small — for a 30-minute walk at a high body mass, resting metabolism alone
    is a fifth of the total.
    """

    NOTE = (
        "Gross includes the calories you would have burned at rest over the same "
        "time; net is the extra cost of the walk itself."
    )

    def __init__(self, effort):
        """Capture both figures from one walk's effort."""
        self.kcal_gross = effort.kcal_gross
        self.kcal_net = effort.kcal_net

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "kcal_gross": round(self.kcal_gross),
            "kcal_net_of_resting": round(self.kcal_net),
            "note": self.NOTE,
        }


class JointLoadReport:
    """Peak knee loading, and what changes it.

    Included because for the heaviest users it is the thing most likely to end a
    walking habit, and because the weight-loss ratio is genuinely motivating in
    a way calorie counts are not.
    """

    NOTE = (
        "Peak knee compressive force is roughly 3x body weight walking on the "
        "level and rises braking downhill. Each pound of body weight is about "
        "four pounds of knee load per step, so the same route gets easier on the "
        "joints as weight comes down."
    )

    def __init__(self, profile, effort):
        """Scale the effort's peak multiple by this walker's body weight."""
        self.peak_bw = effort.knee_load_peak_bw
        self.peak_kg = profile.weight_kg * self.peak_bw

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "peak_knee_force_bw": round(self.peak_bw, 1),
            "peak_knee_force_kg": round(self.peak_kg),
            "peak_knee_force_lb": round(UnitConverter.kg_to_lb(self.peak_kg)),
            "note": self.NOTE,
        }


class WeightProjection:
    """What repeating this walk would do to body weight, projected honestly.

    This is the number people actually want, and it is the easiest one in the
    whole app to get badly wrong. Two things are done differently from the
    typical calorie calculator:

    **The 3,500 kcal per pound rule is not used for long horizons.** It treats
    energy balance as static, ignoring that resting metabolism and the energy
    cost of movement both fall as body mass does. Hall et al. (2011) showed it
    substantially overestimates: applied for a year it predicts roughly twice
    the weight loss that actually occurs. It is a reasonable approximation over
    a few weeks, so it is used only there, and labelled as short-run.

    **The long-run figure uses Hall's rule of thumb** — a sustained change of
    about 10 kcal/day produces an eventual change of about 1 lb, with half of
    it reached in roughly a year and 95% within three. That is the number that
    describes where someone actually ends up.

    **Net calories, not gross.** Only the energy above what the body would have
    spent resting anyway can contribute to a deficit. Using the gross figure —
    as most calculators do — inflates every projection.

    The caveat that matters most is behavioural rather than metabolic:
    appetite compensation. People eat back a substantial share of what exercise
    burns, often around half, and the projections below assume they do not. It
    is stated rather than buried.
    """

    #: The classic static rule. Retained only for the short-run figure, where
    #: metabolic adaptation has not yet had time to matter much.
    KCAL_PER_LB_STATIC = 3500.0

    #: Hall's rule of thumb: a sustained 10 kcal/day produces ~1 lb of eventual
    #: body-weight change.
    KCAL_PER_DAY_PER_LB_EVENTUAL = 10.0

    #: Roughly half the eventual change is reached within a year.
    FRACTION_AT_ONE_YEAR = 0.5

    #: Weekly frequencies to project. Three sessions is the usual starting
    #: prescription; five is the WHO pattern; seven is daily.
    DEFAULT_FREQUENCIES = (3, 5, 7)

    #: Below this weekly deficit the projection is noise rather than signal,
    #: and presenting a number would imply precision that is not there.
    MIN_WEEKLY_KCAL = 200.0

    def __init__(self, profile, effort, frequencies=DEFAULT_FREQUENCIES):
        """Project weight change for one walk repeated at several frequencies."""
        self.profile = profile
        self.effort = effort
        self.frequencies = tuple(frequencies)

    def for_frequency(self, sessions_per_week: int) -> dict[str, Any]:
        """Project the effect of walking this route N times a week."""
        weekly_kcal = self.effort.kcal_net * sessions_per_week
        daily_kcal = weekly_kcal / 7.0

        # Short run: the static rule, over four weeks, where it is defensible.
        four_week_lb = (weekly_kcal * 4.0) / self.KCAL_PER_LB_STATIC
        # Long run: where this actually settles, per Hall.
        eventual_lb = daily_kcal / self.KCAL_PER_DAY_PER_LB_EVENTUAL
        one_year_lb = eventual_lb * self.FRACTION_AT_ONE_YEAR

        return {
            "sessions_per_week": sessions_per_week,
            "weekly_kcal": round(weekly_kcal),
            "first_month_lb": round(four_week_lb, 1),
            "one_year_lb": round(one_year_lb, 1),
            "eventual_lb": round(eventual_lb, 1),
            "eventual_pct_of_body_weight": round(
                100.0 * eventual_lb / UnitConverter.kg_to_lb(self.profile.weight_kg), 1
            ),
            "meaningful": weekly_kcal >= self.MIN_WEEKLY_KCAL,
        }

    @property
    def is_meaningful(self) -> bool:
        """Whether any projected frequency clears the noise floor."""
        return any(self.for_frequency(f)["meaningful"] for f in self.frequencies)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "projections": [self.for_frequency(f) for f in self.frequencies],
            "basis": "net",
            "note": (
                "Based on the calories this walk costs above resting, assuming "
                "what you eat does not change."
            ),
            "method": (
                "Short-run figures use the conventional 3,500 kcal per pound. "
                "Longer horizons use Hall et al. (2011), where a sustained "
                "10 kcal/day produces about 1 lb of eventual change — the "
                "3,500 rule roughly doubles real one-year loss because it "
                "ignores the fall in metabolism as weight comes off."
            ),
            "caveats": [
                "Assumes your eating does not change. In practice people eat "
                "back a large share of what exercise burns, often about half, "
                "which is the single biggest reason real loss undershoots.",
                "Weight change is not linear and plateaus as body mass falls.",
                "Individual response varies widely; this is a population "
                "average, not a prediction about you.",
            ],
        }


class HealthReport:
    """Everything the app is willing to claim about one walk.

    Composes the individual guideline models and attaches the caveats. The
    caveats are part of the model rather than frontend copy on purpose: they
    travel with the numbers, so an API consumer cannot take the figures without
    also receiving their limits.
    """

    BASE_CAVEATS = (
        "Estimates for a healthy adult, not medical advice.",
        "Speed is a population mean for your age and sex; individuals vary by roughly +/-0.2 m/s.",
        "Energy cost uses Minetti et al. (2002), measured on trained adults; "
        "absolute values carry more uncertainty at high BMI.",
    )

    HEIGHT_CAVEAT = "Height was assumed from population averages — enter it for a better estimate."

    def __init__(self, profile, effort):
        """Assemble the report for one walker and one walk."""
        self.profile = profile
        self.effort = effort
        self.guideline = GuidelineProgress(effort)
        self.steps = StepProgress(effort)
        self.energy = EnergyReport(effort)
        self.joint_load = JointLoadReport(profile, effort)
        self.weight = WeightProjection(profile, effort)

    @property
    def caveats(self) -> list[str]:
        """The limits that must travel with these numbers."""
        caveats = list(self.BASE_CAVEATS)
        if self.profile.height_assumed:
            caveats.append(self.HEIGHT_CAVEAT)
        return caveats

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "guideline_progress": self.guideline.to_dict(),
            "steps": self.steps.to_dict(),
            "energy": self.energy.to_dict(),
            "joint_load": self.joint_load.to_dict(),
            "weight_projection": self.weight.to_dict(),
            "caveats": self.caveats,
        }
