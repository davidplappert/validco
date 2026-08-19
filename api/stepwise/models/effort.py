"""What a walk costs the person walking it.

:class:`WalkEffort` is the result of pushing a route's shape through the
physiology models. :class:`WalkSegment` is the unit it works in — one graph edge
with its length and its rise.

The reason the calculation is per-segment rather than from route totals is worth
stating plainly, because it is the difference between this model and a naive
one: a route that climbs 40 m and descends 40 m has zero net elevation change
and is materially harder than flat ground. Averaging the gradient first would
erase exactly the thing the app exists to tell you about.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from ..physiology.anthropometry import UnitConverter
from ..physiology.energy import DEFAULT_COST_MODEL, GradientCostModel

LOG = logging.getLogger(__name__)

JOULES_PER_KCAL = 4184.0

#: Conventional intensity thresholds in METs.
MET_MODERATE = 3.0
MET_VIGOROUS = 6.0

#: Peak tibiofemoral compressive force in level walking, in multiples of body
#: weight (Messier et al. and subsequent musculoskeletal modelling).
KNEE_LOAD_LEVEL_BW = 3.0

#: How much a steep descent raises that peak, per unit of downhill gradient.
KNEE_LOAD_DESCENT_FACTOR = 1.6


class WalkSegment(NamedTuple):
    """One stretch of walking: horizontal distance and elevation change."""

    distance_m: float
    rise_m: float

    @property
    def grade(self) -> float:
        """Rise over run, or zero for a degenerate segment."""
        return self.rise_m / self.distance_m if self.distance_m > 0 else 0.0


class WalkEffort:
    """The physiological result of walking a specific sequence of graded segments.

    Built by :meth:`evaluate`, which is the only supported way to make one — the
    fields are interdependent and constructing them by hand would let them drift
    out of agreement.
    """

    __slots__ = (
        "distance_m",
        "duration_s",
        "ascent_m",
        "descent_m",
        "kcal_gross",
        "kcal_net",
        "steps",
        "mets",
        "personal_mets",
        "avg_speed_ms",
        "peak_grade",
        "steepest_descent",
        "knee_load_peak_bw",
    )

    def __init__(self, **fields: Any):
        """Assign the computed fields. Prefer :meth:`evaluate`."""
        for name in self.__slots__:
            setattr(self, name, fields[name])

    @classmethod
    def evaluate(
        cls,
        profile,
        segments: list[WalkSegment] | list[tuple[float, float]],
        *,
        pause_s: float = 0.0,
        cost_model: GradientCostModel | None = None,
    ) -> WalkEffort:
        """Score a walk from its shape.

        ``segments`` is one entry per graph edge. ``pause_s`` covers time not
        spent walking — waiting at signalised crossings — which counts against
        the clock but adds only resting calories, not movement calories.

        Raises ``ValueError`` on an empty walk: there is no meaningful zero
        value for a route, and returning one would let an empty result render as
        a real suggestion.
        """
        if not segments:
            raise ValueError("a walk needs at least one segment")

        cost_model = cost_model or DEFAULT_COST_MODEL
        mass = profile.weight_kg

        total_distance = 0.0
        total_time = 0.0
        joules = 0.0
        ascent = 0.0
        descent = 0.0
        peak_grade = 0.0
        steepest_descent = 0.0
        total_steps = 0

        for raw in segments:
            segment = raw if isinstance(raw, WalkSegment) else WalkSegment(*raw)
            if segment.distance_m <= 0.0:
                continue

            # Clamping here rather than at the call site means DEM noise on a
            # one-metre edge cannot produce a 4000% gradient and an infinite
            # duration.
            grade = cost_model.clamp(segment.grade)

            total_time += segment.distance_m / profile.speed_on_grade_ms(grade)
            joules += cost_model.cost_j_per_kg_m(grade) * mass * segment.distance_m
            total_distance += segment.distance_m
            total_steps += profile.steps_for(segment.distance_m, grade)

            if segment.rise_m > 0:
                ascent += segment.rise_m
            else:
                descent += -segment.rise_m

            peak_grade = max(peak_grade, abs(grade))
            steepest_descent = min(steepest_descent, grade)

        total_time += pause_s

        kcal_net = joules / JOULES_PER_KCAL
        kcal_gross = kcal_net + profile.resting_kcal(total_time)
        hours = total_time / 3600.0

        effort = cls(
            distance_m=total_distance,
            duration_s=total_time,
            ascent_m=ascent,
            descent_m=descent,
            kcal_gross=kcal_gross,
            kcal_net=kcal_net,
            steps=total_steps,
            # Classic MET: kcal/hr per kg of body mass. Every fitness tracker
            # reports this, so it is reported here for comparability.
            mets=(kcal_gross / hours) / mass if hours > 0 else 0.0,
            # Personal MET: expenditure as a multiple of *this person's* resting
            # rate, which is what "how hard is this for me" actually means. The
            # two diverge at high BMI, and this is the honest one.
            personal_mets=(
                (kcal_gross / hours) / (profile.rmr_kcal_day / 24.0) if hours > 0 else 0.0
            ),
            avg_speed_ms=total_distance / total_time if total_time > 0 else 0.0,
            peak_grade=peak_grade,
            steepest_descent=steepest_descent,
            knee_load_peak_bw=cls._knee_load(steepest_descent),
        )
        LOG.debug(
            "effort evaluated segments=%d dist=%.0fm dur=%.0fs asc=%.0fm kcal=%.0f met=%.1f",
            len(segments),
            total_distance,
            total_time,
            ascent,
            kcal_gross,
            effort.mets,
        )
        return effort

    @staticmethod
    def _knee_load(steepest_descent: float) -> float:
        """Peak knee compressive force in multiples of body weight.

        Level walking sits near 3x. Braking on a descent is the loading spike,
        so the steepest downhill on the route drives the peak rather than the
        average gradient.
        """
        return KNEE_LOAD_LEVEL_BW * (
            1.0 + KNEE_LOAD_DESCENT_FACTOR * abs(min(0.0, steepest_descent))
        )

    # --- derived views ----------------------------------------------------

    @property
    def duration_min(self) -> float:
        """Duration in minutes."""
        return self.duration_s / 60.0

    @property
    def intensity(self) -> str:
        """Intensity band: ``light``, ``moderate`` or ``vigorous``."""
        if self.mets >= MET_VIGOROUS:
            return "vigorous"
        return "moderate" if self.mets >= MET_MODERATE else "light"

    @property
    def counts_as_moderate(self) -> bool:
        """Whether this walk can count toward the WHO moderate-activity target."""
        return self.mets >= MET_MODERATE

    @property
    def climb_per_km(self) -> float:
        """Metres of ascent per kilometre — a scale-free measure of hilliness."""
        return self.ascent_m / max(0.1, self.distance_m / 1000.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response, in both unit systems."""
        return {
            "distance_m": round(self.distance_m),
            "distance_mi": round(UnitConverter.m_to_miles(self.distance_m), 2),
            "duration_s": round(self.duration_s),
            "duration_min": round(self.duration_min, 1),
            "ascent_m": round(self.ascent_m),
            "descent_m": round(self.descent_m),
            "ascent_ft": round(UnitConverter.m_to_ft(self.ascent_m)),
            "descent_ft": round(UnitConverter.m_to_ft(self.descent_m)),
            "kcal_gross": round(self.kcal_gross),
            "kcal_net": round(self.kcal_net),
            "steps": self.steps,
            "mets": round(self.mets, 1),
            "personal_mets": round(self.personal_mets, 1),
            "intensity": self.intensity,
            "avg_speed_ms": round(self.avg_speed_ms, 2),
            "avg_speed_mph": round(UnitConverter.ms_to_mph(self.avg_speed_ms), 2),
            "avg_pace_min_per_mi": (
                round(26.8224 / self.avg_speed_ms, 1) if self.avg_speed_ms > 0 else 0.0
            ),
            "peak_grade_pct": round(self.peak_grade * 100.0, 1),
            "climb_per_km_m": round(self.climb_per_km),
            "knee_load_peak_bw": round(self.knee_load_peak_bw, 1),
        }

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return (
            f"WalkEffort(dist={self.distance_m:.0f}m, dur={self.duration_min:.0f}min, "
            f"kcal={self.kcal_gross:.0f}, met={self.mets:.1f})"
        )
