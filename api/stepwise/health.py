"""The physiology behind StepWise: how long a walk takes and what it does for you.

This module is deliberately the most heavily-cited code in the project, because
"how many calories was that walk" is the kind of number apps routinely get wrong
by 30% and state to three significant figures anyway. Every coefficient below
traces to a specific published source, and the places where the literature says
the model is weak are called out rather than papered over.

The model in one paragraph
--------------------------
Walking speed comes from population norms for the user's age and sex, scaled
down for body mass and scaled by terrain gradient. Energy comes from Minetti's
measured cost-of-transport curve, which — unlike the usual ACSM equation — is
defined for *downhill* as well as uphill, and is applied to body mass separately
from resting metabolism, which is estimated with an equation validated in
obesity. Health framing comes from the 2025 Lancet Public Health step
dose-response meta-analysis and the WHO 2020 activity guidelines.

Sources
-------
* Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. "Energy cost of walking and
  running at extreme uphill and downhill slopes." J Appl Physiol 93:1039-1046,
  2002. — the gradient cost polynomial, measured over -45% to +45%.
* Bohannon RW, Andrews AW. "Normal walking speed: a descriptive meta-analysis."
  Physiotherapy 97(3):182-189, 2011. — comfortable gait speed by age and sex.
* Tobler W. "Three presentations on geographical analysis and modeling." 1993.
  — the exponential slope/speed relationship (used for its *shape* only).
* Mifflin MD, St Jeor ST, et al. "A new predictive equation for resting energy
  expenditure in healthy individuals." Am J Clin Nutr 51:241-247, 1990.
* Deurenberg P, Weststrate JA, Seidell JC. "Body mass index as a measure of body
  fatness." Br J Nutr 65:105-114, 1991. — body-fat estimate from BMI/age/sex.
* Ding D, et al. "Daily steps and health outcomes in adults: a systematic review
  and dose-response meta-analysis." Lancet Public Health, 2025. — the 7,000
  steps/day inflection; 7,000 vs 2,000 steps = 47% lower all-cause mortality.
* WHO. "Guidelines on physical activity and sedentary behaviour." 2020.
  — 150-300 min/week moderate-intensity aerobic activity for adults 18-64.
* Messier SP, et al. "Weight loss reduces knee-joint loads in overweight and
  obese older adults with knee osteoarthritis." Arthritis Rheum 52:2026-2032,
  2005. — the ~4:1 knee-load-to-body-weight ratio per step.
* Ling W, et al. "Gait and Function in Class III Obesity." J Obesity, 2012, and
  Browning RC, Kram R. — reduced preferred speed and step length with obesity.

Known limitations (surfaced to the user, not hidden)
----------------------------------------------------
* Minetti's subjects were 10 trained male runners. The *shape* of the gradient
  curve generalises well and is the standard reference, but absolute cost for a
  deconditioned or high-BMI walker carries real uncertainty.
* Predicted speed is a population mean. Individual comfortable pace varies by
  roughly +/-0.2 m/s around it.
* Every output is an estimate for a healthy adult and is not medical advice.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Literal

LOG = logging.getLogger(__name__)

Sex = Literal["male", "female"]

# --- constants -------------------------------------------------------------

JOULES_PER_KCAL = 4184.0
KG_PER_LB = 0.45359237
CM_PER_INCH = 2.54

# Mean adult stature, NHANES 2015-2018 (US, 20+). Only used when the user does
# not supply a height; it feeds resting metabolism and step length.
DEFAULT_HEIGHT_CM = {"male": 175.3, "female": 161.3}

# Bohannon & Andrews (2011) comfortable gait speed, m/s, keyed by decade start.
# Speed is flat through middle age and falls away steeply after 70.
_GAIT_SPEED = {
    "male": {20: 1.358, 30: 1.433, 40: 1.434, 50: 1.433, 60: 1.339, 70: 1.262, 80: 0.968},
    "female": {20: 1.341, 30: 1.337, 40: 1.390, 50: 1.313, 60: 1.241, 70: 1.132, 80: 0.943},
}

# WHO 2020: 150-300 min/week of moderate-intensity aerobic activity.
WHO_WEEKLY_MODERATE_MIN = 150
WHO_WEEKLY_MODERATE_MIN_UPPER = 300

# Lancet Public Health 2025 dose-response: benefits inflect around 7,000/day.
DAILY_STEP_TARGET = 7000

# Conventional intensity bands, in METs.
MET_MODERATE = 3.0
MET_VIGOROUS = 6.0

# Minetti et al. (2002), Table: Cw(i) in J/kg/m, i = gradient as rise/run.
# Fitted R^2 = 0.999 over -0.45 <= i <= +0.45. This is *net* cost — it excludes
# resting metabolism, which we add separately.
_MINETTI = (280.5, -58.7, -76.8, 51.9, 19.6, 2.5)
MINETTI_GRADE_LIMIT = 0.45

# Peak tibiofemoral compressive force in level walking, in multiples of body
# weight. Messier et al. and subsequent modelling put level walking near 3x.
KNEE_LOAD_LEVEL_BW = 3.0


def minetti_cost(grade: float) -> float:
    """Net metabolic cost of walking, J per kg of body mass per metre travelled.

    ``grade`` is rise over run: +0.10 is a 10% climb, -0.10 a 10% descent.

    The curve is not symmetric and not monotonic, which is exactly why it is
    worth using. Cost bottoms out around a 10% *descent* (0.81 J/kg/m — cheaper
    than walking on the flat, because gravity does some of the work) and climbs
    steeply again on steeper descents as the legs have to absorb energy
    eccentrically. A model that treats downhill as "free" or as a linear
    negative — as the ACSM equation implicitly does — gets San Francisco badly
    wrong in both directions.
    """
    i = max(-MINETTI_GRADE_LIMIT, min(MINETTI_GRADE_LIMIT, grade))
    a, b, c, d, e, f = _MINETTI
    cost = ((((a * i + b) * i + c) * i + d) * i + e) * i + f
    # The polynomial is a regression, not a physical law; it can dip fractionally
    # negative just outside the sampled band. Walking is never free.
    return max(cost, 0.3)


def tobler_speed_factor(grade: float) -> float:
    """Speed multiplier for gradient, normalised so flat ground == 1.0.

    Tobler's hiking function is ``W = 6 * exp(-3.5 * |i + 0.05|)`` km/h, whose
    absolute values describe a fit hiker (5 km/h on the flat) rather than a
    typical person. We keep only its *shape* and let the user's own predicted
    flat speed set the scale, which composes correctly with the age, sex, and
    body-mass adjustments below.

    The ``+0.05`` offset is the empirical observation that walking is fastest on
    a slight downhill (about -2.9 degrees), not on the level.
    """
    return math.exp(-3.5 * (abs(grade + 0.05) - 0.05))


@dataclass(frozen=True)
class Profile:
    """A user's physiology, and everything derivable from it.

    Only sex, age, and weight are required — those are what the app asks for.
    Height materially improves resting metabolism and step count, so it is
    accepted when known and defaulted to the population mean when not.
    """

    sex: Sex
    age_years: int
    weight_kg: float
    height_cm: float | None = None
    height_assumed: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "height_assumed", self.height_cm is None)
        if self.height_cm is None:
            object.__setattr__(self, "height_cm", DEFAULT_HEIGHT_CM[self.sex])

    # --- body composition ------------------------------------------------

    @property
    def bmi(self) -> float:
        h_m = self.height_cm / 100.0
        return self.weight_kg / (h_m * h_m)

    @property
    def body_fat_pct(self) -> float:
        """Deurenberg (1991) estimate from BMI, age and sex.

        Population-level only: the standard error is around 4 percentage points
        for an individual. It is used here to *explain* the resting-metabolism
        adjustment, never as a headline number.
        """
        sex_term = 1.0 if self.sex == "male" else 0.0
        pct = 1.20 * self.bmi + 0.23 * self.age_years - 10.8 * sex_term - 5.4
        return max(3.0, min(70.0, pct))

    @property
    def fat_free_mass_kg(self) -> float:
        return self.weight_kg * (1.0 - self.body_fat_pct / 100.0)

    @property
    def rmr_kcal_day(self) -> float:
        """Resting metabolic rate, Mifflin-St Jeor.

        Chosen over the ACSM equation's flat 3.5 mL/kg/min because that constant
        scales resting metabolism linearly with *total* mass. Adipose tissue is
        far less metabolically active than muscle, so for a high-BMI user the
        ACSM resting term alone can overstate expenditure substantially — the
        documented failure mode of ACSM equations in obesity. Mifflin-St Jeor
        scales sub-linearly with mass and is validated across BMI classes.
        """
        base = 10.0 * self.weight_kg + 6.25 * self.height_cm - 5.0 * self.age_years
        return base + (5.0 if self.sex == "male" else -161.0)

    @property
    def rmr_kcal_min(self) -> float:
        return self.rmr_kcal_day / 1440.0

    # --- gait -------------------------------------------------------------

    @property
    def baseline_speed_ms(self) -> float:
        """Comfortable walking speed on flat ground, m/s.

        Starts from the Bohannon & Andrews norm for this age and sex, then
        applies a body-mass penalty. Preferred walking speed falls with obesity
        class — an active strategy to cut joint loading and metabolic cost, not
        a lack of effort — and the reduction is small at overweight but clear by
        class III.
        """
        table = _GAIT_SPEED[self.sex]
        decade = max(20, min(80, (self.age_years // 10) * 10))
        speed = table[decade]

        # Linear interpolation between decades, so a 39-year-old is not
        # discontinuously different from a 40-year-old.
        nxt = decade + 10
        if nxt in table:
            frac = (self.age_years - decade) / 10.0
            speed += (table[nxt] - speed) * frac

        speed *= self._bmi_speed_factor()
        return max(0.4, speed)

    def _bmi_speed_factor(self) -> float:
        """Multiplier on comfortable speed for body mass.

        Anchored on reported preferred-speed differences by obesity class:
        negligible below BMI 30, roughly -6% at class I, -12% at class II, and
        -18% or more at class III, where gait changes qualitatively.
        """
        bmi = self.bmi
        if bmi < 25.0:
            return 1.0
        if bmi < 30.0:
            return 1.0 - 0.010 * (bmi - 25.0) / 5.0 * 3.0  # up to -3% across overweight
        if bmi < 35.0:
            return 0.970 - 0.030 * (bmi - 30.0) / 5.0
        if bmi < 40.0:
            return 0.940 - 0.060 * (bmi - 35.0) / 5.0
        # Class III: the reduction continues but flattens; floor it so the model
        # stays physically sensible at extreme BMI.
        return max(0.72, 0.880 - 0.030 * (bmi - 40.0) / 5.0)

    @property
    def step_length_m(self) -> float:
        """Step length on the flat.

        Base ratio of step length to stature is about 0.415 for men and 0.413
        for women. Obesity reduces step length by a further 5-10 cm as part of
        the same stability-and-economy adaptation that reduces speed.
        """
        base = self.height_cm / 100.0 * (0.415 if self.sex == "male" else 0.413)
        bmi = self.bmi
        if bmi >= 40.0:
            base -= 0.09
        elif bmi >= 35.0:
            base -= 0.07
        elif bmi >= 30.0:
            base -= 0.05
        return max(0.35, base)

    def describe(self) -> dict:
        return {
            "sex": self.sex,
            "age_years": self.age_years,
            "weight_kg": round(self.weight_kg, 1),
            "height_cm": round(self.height_cm, 1),
            "height_assumed": self.height_assumed,
            "bmi": round(self.bmi, 1),
            "bmi_class": bmi_class(self.bmi),
            "body_fat_pct_est": round(self.body_fat_pct, 1),
            "rmr_kcal_day": round(self.rmr_kcal_day),
            "baseline_speed_ms": round(self.baseline_speed_ms, 3),
            "baseline_speed_mph": round(self.baseline_speed_ms * 2.23694, 2),
            "step_length_m": round(self.step_length_m, 3),
        }


def bmi_class(bmi: float) -> str:
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


@dataclass
class WalkEffort:
    """The physiological result of walking a specific sequence of graded steps."""

    distance_m: float
    duration_s: float
    ascent_m: float
    descent_m: float
    kcal_gross: float
    kcal_net: float
    steps: int
    mets: float
    personal_mets: float
    avg_speed_ms: float
    peak_grade: float
    knee_load_peak_bw: float

    def to_dict(self) -> dict:
        return {
            "distance_m": round(self.distance_m),
            "distance_mi": round(self.distance_m / 1609.344, 2),
            "duration_s": round(self.duration_s),
            "duration_min": round(self.duration_s / 60.0, 1),
            "ascent_m": round(self.ascent_m),
            "descent_m": round(self.descent_m),
            "ascent_ft": round(self.ascent_m * 3.28084),
            "descent_ft": round(self.descent_m * 3.28084),
            "kcal_gross": round(self.kcal_gross),
            "kcal_net": round(self.kcal_net),
            "steps": self.steps,
            "mets": round(self.mets, 1),
            "personal_mets": round(self.personal_mets, 1),
            "intensity": intensity_band(self.mets),
            "avg_speed_ms": round(self.avg_speed_ms, 2),
            "avg_speed_mph": round(self.avg_speed_ms * 2.23694, 2),
            "avg_pace_min_per_mi": round(26.8224 / self.avg_speed_ms, 1),
            "peak_grade_pct": round(self.peak_grade * 100.0, 1),
            "knee_load_peak_bw": round(self.knee_load_peak_bw, 1),
        }


def intensity_band(mets: float) -> str:
    if mets >= MET_VIGOROUS:
        return "vigorous"
    if mets >= MET_MODERATE:
        return "moderate"
    return "light"


def evaluate_walk(
    profile: Profile,
    steps: list[tuple[float, float]],
    *,
    pause_s: float = 0.0,
) -> WalkEffort:
    """Score a walk given its shape.

    ``steps`` is a list of ``(horizontal_distance_m, elevation_change_m)`` pairs
    — one per graph edge along the route. Working edge by edge rather than from
    route totals is the whole point: a route that climbs 40 m and descends 40 m
    is much harder than its net-zero elevation change suggests, and averaging
    the gradient first would hide that completely.

    ``pause_s`` accounts for time not spent walking (waiting at crossings).
    """
    if not steps:
        raise ValueError("a walk needs at least one step")

    v_flat = profile.baseline_speed_ms
    mass = profile.weight_kg

    total_dist = 0.0
    total_time = 0.0
    joules = 0.0
    ascent = 0.0
    descent = 0.0
    peak_grade = 0.0
    steepest_descent = 0.0
    total_steps = 0

    for dist_m, rise_m in steps:
        if dist_m <= 0.0:
            continue
        grade = rise_m / dist_m
        # Guard against DEM noise on very short edges producing absurd gradients.
        grade = max(-MINETTI_GRADE_LIMIT, min(MINETTI_GRADE_LIMIT, grade))

        speed = max(0.25, v_flat * tobler_speed_factor(grade))
        total_time += dist_m / speed
        joules += minetti_cost(grade) * mass * dist_m
        total_dist += dist_m

        if rise_m > 0:
            ascent += rise_m
        else:
            descent += -rise_m

        peak_grade = max(peak_grade, abs(grade))
        steepest_descent = min(steepest_descent, grade)

        # Step length shortens uphill and on steep descents.
        step_len = profile.step_length_m * (0.85 if abs(grade) > 0.08 else 1.0)
        total_steps += int(dist_m / step_len)

    total_time += pause_s

    kcal_net = joules / JOULES_PER_KCAL
    kcal_rest = profile.rmr_kcal_min * (total_time / 60.0)
    kcal_gross = kcal_net + kcal_rest

    hours = total_time / 3600.0
    # Classic MET: kcal/hr per kg of body mass. This is the number every fitness
    # tracker reports, so we report it for comparability.
    mets = (kcal_gross / hours) / mass if hours > 0 else 0.0
    # Personal MET: expenditure as a multiple of *this person's* measured resting
    # rate, which is what "how hard is this for me" actually means. For a
    # high-BMI user the two diverge, and the personal figure is the honest one.
    personal_mets = (kcal_gross / hours) / (profile.rmr_kcal_day / 24.0) if hours > 0 else 0.0

    # Downhill walking raises peak knee compressive force well above the ~3x body
    # weight of level walking; braking on a steep descent is the loading spike.
    knee_load = KNEE_LOAD_LEVEL_BW * (1.0 + 1.6 * abs(min(0.0, steepest_descent)))

    effort = WalkEffort(
        distance_m=total_dist,
        duration_s=total_time,
        ascent_m=ascent,
        descent_m=descent,
        kcal_gross=kcal_gross,
        kcal_net=kcal_net,
        steps=total_steps,
        mets=mets,
        personal_mets=personal_mets,
        avg_speed_ms=total_dist / total_time if total_time > 0 else 0.0,
        peak_grade=peak_grade,
        knee_load_peak_bw=knee_load,
    )
    LOG.debug(
        "evaluate_walk edges=%d dist=%.0fm dur=%.0fs asc=%.0fm kcal=%.0f mets=%.1f",
        len(steps), total_dist, total_time, ascent, kcal_gross, mets,
    )
    return effort


def health_effects(profile: Profile, effort: WalkEffort) -> dict:
    """Translate one walk into the outcomes the evidence base actually supports.

    Deliberately framed as "this walk is N% of a guideline", not as invented
    claims about weight loss or life expectancy from a single walk.
    """
    minutes = effort.duration_s / 60.0
    moderate_minutes = minutes if effort.mets >= MET_MODERATE else 0.0
    met_minutes = effort.mets * minutes

    knee_load_kg = profile.weight_kg * effort.knee_load_peak_bw

    return {
        "guideline_progress": {
            # WHO 2020: 150-300 min/week moderate aerobic activity, 18-64.
            "who_weekly_moderate_min": WHO_WEEKLY_MODERATE_MIN,
            "moderate_minutes": round(moderate_minutes, 1),
            "pct_of_weekly_target": round(
                100.0 * moderate_minutes / WHO_WEEKLY_MODERATE_MIN, 1
            ),
            "met_minutes": round(met_minutes),
            "counts_as_moderate": effort.mets >= MET_MODERATE,
        },
        "steps": {
            # Lancet Public Health 2025 dose-response meta-analysis.
            "walk_steps": effort.steps,
            "daily_target": DAILY_STEP_TARGET,
            "pct_of_daily_target": round(100.0 * effort.steps / DAILY_STEP_TARGET, 1),
            "context": (
                "7,000 steps/day is where most outcomes plateau; versus 2,000 "
                "steps/day it tracks with 47% lower all-cause mortality and 25% "
                "lower cardiovascular disease risk."
            ),
        },
        "energy": {
            "kcal_gross": round(effort.kcal_gross),
            "kcal_net_of_resting": round(effort.kcal_net),
            "note": (
                "Gross includes the calories you would have burned at rest over "
                "the same time; net is the extra cost of the walk itself."
            ),
        },
        "joint_load": {
            "peak_knee_force_bw": round(effort.knee_load_peak_bw, 1),
            "peak_knee_force_kg": round(knee_load_kg),
            "peak_knee_force_lb": round(knee_load_kg / KG_PER_LB),
            "note": (
                "Peak knee compressive force is roughly 3x body weight walking "
                "on the level and rises braking downhill. Each pound of body "
                "weight is about four pounds of knee load per step, so the same "
                "route gets easier on the joints as weight comes down."
            ),
        },
        "caveats": [
            "Estimates for a healthy adult, not medical advice.",
            "Speed is a population mean for your age and sex; individuals vary "
            "by roughly +/-0.2 m/s.",
            "Energy cost uses Minetti et al. (2002), measured on trained adults; "
            "absolute values carry more uncertainty at high BMI.",
        ]
        + (
            ["Height was assumed from population averages — enter it for a better estimate."]
            if profile.height_assumed
            else []
        ),
    }


def suitability(profile: Profile, effort: WalkEffort) -> dict:
    """How well a specific route fits this specific person.

    Returns a 0-100 score plus plain-language reasons. The scoring is opinionated
    on purpose: for a deconditioned or high-BMI walker, a route with a brutal
    climb is a worse recommendation than a longer flat one, even though the
    climb "burns more calories".
    """
    score = 100.0
    notes: list[str] = []
    bmi = profile.bmi

    # Steep grades are the dominant comfort factor, and more so at high BMI.
    grade_tolerance = 0.10 if bmi < 30 else (0.07 if bmi < 40 else 0.05)
    if effort.peak_grade > grade_tolerance:
        over = (effort.peak_grade - grade_tolerance) * 100.0
        score -= min(35.0, over * 3.5)
        notes.append(
            f"Includes a {effort.peak_grade * 100:.0f}% grade, steeper than the "
            f"{grade_tolerance * 100:.0f}% that suits your profile."
        )

    # Total climb, scaled to the distance walked.
    climb_per_km = effort.ascent_m / max(0.1, effort.distance_m / 1000.0)
    if climb_per_km > 25.0:
        score -= min(20.0, (climb_per_km - 25.0) * 0.5)
        notes.append(f"Hilly overall — {climb_per_km:.0f} m of climb per km.")

    # Duration relative to what is reasonable to start with.
    minutes = effort.duration_s / 60.0
    comfortable_max = 60.0 if bmi < 30 else (45.0 if bmi < 40 else 30.0)
    if minutes > comfortable_max:
        score -= min(20.0, (minutes - comfortable_max) * 0.5)
        notes.append(
            f"At {minutes:.0f} minutes this is a long outing to start with; "
            f"around {comfortable_max:.0f} minutes is a gentler build-up."
        )

    if effort.mets < MET_MODERATE:
        notes.append(
            "This pace lands below the 3 MET moderate-intensity threshold, so it "
            "will not count toward the WHO weekly target — though it still counts "
            "toward your daily steps."
        )

    if bmi >= 40 and effort.knee_load_peak_bw > 3.5:
        notes.append(
            "Steep descents raise knee loading noticeably; taking downhills "
            "slowly, or picking a flatter return leg, reduces the peak."
        )

    score = max(0.0, min(100.0, score))
    if not notes:
        notes.append("Grade, length and intensity all sit in a comfortable range for you.")

    LOG.debug("suitability score=%.0f notes=%d bmi=%.1f", score, len(notes), bmi)
    return {"score": round(score), "notes": notes}


# --- unit helpers used by the API layer ------------------------------------


def lb_to_kg(lb: float) -> float:
    return lb * KG_PER_LB


def ft_in_to_cm(feet: float, inches: float = 0.0) -> float:
    return (feet * 12.0 + inches) * CM_PER_INCH
