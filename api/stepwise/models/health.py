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
            "caveats": self.caveats,
        }
