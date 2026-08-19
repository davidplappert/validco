"""Whether a specific route suits a specific walker.

Scoring is a list of :class:`SuitabilityRule` objects rather than a chain of
``if`` statements. That shape buys three things: each rule states its own reason
in plain language, rules can be tested in isolation, and adding a consideration
(weather, daylight, surface accessibility) is an append rather than a surgery.

The scoring is opinionated on purpose. For a deconditioned or high-BMI walker a
route with a brutal climb is a *worse* recommendation than a longer flat one,
even though the climb burns more calories. An app that ranked purely on calories
would send exactly the wrong person up Filbert Street.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

LOG = logging.getLogger(__name__)


class SuitabilityRule(ABC):
    """One consideration in whether a route fits a walker."""

    #: Most this rule may deduct from the 100-point starting score.
    max_penalty: float = 0.0

    @abstractmethod
    def evaluate(self, profile, effort) -> tuple[float, str | None]:
        """Return ``(penalty, reason)`` for this walker and this walk.

        ``penalty`` is subtracted from the score and must never exceed
        :attr:`max_penalty`. ``reason`` is user-facing text, or ``None`` when
        the rule has nothing to say.
        """


class GradeToleranceRule(SuitabilityRule):
    """Penalises gradients steeper than the walker comfortably handles."""

    max_penalty = 35.0
    PENALTY_PER_PCT = 3.5

    def evaluate(self, profile, effort) -> tuple[float, str | None]:
        """Compare the route's steepest grade against the walker's tolerance."""
        tolerance = profile.grade_tolerance()
        if effort.peak_grade <= tolerance:
            return 0.0, None
        excess_pct = (effort.peak_grade - tolerance) * 100.0
        penalty = min(self.max_penalty, excess_pct * self.PENALTY_PER_PCT)
        return penalty, (
            f"Includes a {effort.peak_grade * 100:.0f}% grade, steeper than the "
            f"{tolerance * 100:.0f}% that suits your profile."
        )


class TotalClimbRule(SuitabilityRule):
    """Penalises cumulative climbing, independent of any single steep pitch.

    A route can be relentlessly rolling without ever exceeding the grade
    tolerance, and that is its own kind of hard.
    """

    max_penalty = 20.0
    COMFORTABLE_M_PER_KM = 25.0
    PENALTY_PER_M = 0.5

    def evaluate(self, profile, effort) -> tuple[float, str | None]:
        """Compare climb-per-kilometre against a comfortable threshold."""
        climb = effort.climb_per_km
        if climb <= self.COMFORTABLE_M_PER_KM:
            return 0.0, None
        penalty = min(self.max_penalty, (climb - self.COMFORTABLE_M_PER_KM) * self.PENALTY_PER_M)
        return penalty, f"Hilly overall — {climb:.0f} m of climb per km."


class DurationRule(SuitabilityRule):
    """Penalises outings longer than a sensible starting point for this walker."""

    max_penalty = 20.0
    PENALTY_PER_MIN = 0.5

    def evaluate(self, profile, effort) -> tuple[float, str | None]:
        """Compare duration against the walker's comfortable single outing."""
        limit = profile.comfortable_duration_min()
        minutes = effort.duration_min
        if minutes <= limit:
            return 0.0, None
        penalty = min(self.max_penalty, (minutes - limit) * self.PENALTY_PER_MIN)
        return penalty, (
            f"At {minutes:.0f} minutes this is a long outing to start with; "
            f"around {limit:.0f} minutes is a gentler build-up."
        )


class IntensityRule(SuitabilityRule):
    """Notes when a walk is too gentle to count toward the weekly guideline.

    Carries no penalty — a light walk is a good walk. It exists so the app does
    not imply guideline credit it cannot claim.
    """

    max_penalty = 0.0

    def evaluate(self, profile, effort) -> tuple[float, str | None]:
        """Flag sub-moderate intensity without penalising it."""
        if effort.counts_as_moderate:
            return 0.0, None
        return 0.0, (
            "This pace lands below the 3 MET moderate-intensity threshold, so it "
            "will not count toward the WHO weekly target — though it still counts "
            "toward your daily steps."
        )


class DescentLoadRule(SuitabilityRule):
    """Warns a high-BMI walker about knee loading on steep descents.

    Advisory rather than penalising: the descent is usually unavoidable if the
    climb happened, and the useful output is technique, not a lower score.
    """

    max_penalty = 0.0
    LOAD_THRESHOLD_BW = 3.5

    def evaluate(self, profile, effort) -> tuple[float, str | None]:
        """Flag heavy descent loading for walkers it most affects."""
        if not profile.is_high_bmi or effort.knee_load_peak_bw <= self.LOAD_THRESHOLD_BW:
            return 0.0, None
        return 0.0, (
            "Steep descents raise knee loading noticeably; taking downhills "
            "slowly, or picking a flatter return leg, reduces the peak."
        )


#: Evaluated in order; the order only affects how reasons are listed.
DEFAULT_RULES: tuple[SuitabilityRule, ...] = (
    GradeToleranceRule(),
    TotalClimbRule(),
    DurationRule(),
    IntensityRule(),
    DescentLoadRule(),
)


class SuitabilityAssessment:
    """A 0-100 fit score with the reasons that produced it.

    The score is never shown without its reasons. A bare number invites more
    trust than this model has earned, and the reasons are the genuinely useful
    output — "includes a 22% grade" tells you something "score: 47" does not.
    """

    STARTING_SCORE = 100.0
    ALL_CLEAR = "Grade, length and intensity all sit in a comfortable range for you."

    def __init__(self, profile, effort, rules: tuple[SuitabilityRule, ...] = DEFAULT_RULES):
        """Run every rule and accumulate the score and the reasons."""
        score = self.STARTING_SCORE
        notes: list[str] = []
        for rule in rules:
            penalty, reason = rule.evaluate(profile, effort)
            score -= penalty
            if reason:
                notes.append(reason)

        self.score = max(0.0, min(100.0, score))
        self.notes = notes or [self.ALL_CLEAR]
        LOG.debug(
            "suitability score=%.0f notes=%d bmi=%.1f", self.score, len(self.notes), profile.bmi
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {"score": round(self.score), "notes": self.notes}
