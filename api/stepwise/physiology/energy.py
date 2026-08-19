"""Metabolic cost of walking as a function of terrain gradient.

Isolated into its own class so the choice of cost curve is a swappable policy
rather than an arithmetic expression buried in the router. Two implementations
ship: the Minetti polynomial used everywhere in the app, and the ACSM equation
kept for comparison and covered by tests that document *why* it is not used.

Sources
-------
Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. "Energy cost of walking and
running at extreme uphill and downhill slopes." J Appl Physiol 93:1039-1046,
2002.

American College of Sports Medicine. *Guidelines for Exercise Testing and
Prescription*, walking metabolic equation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

LOG = logging.getLogger(__name__)


class GradientCostModel(ABC):
    """Strategy interface: gradient in, metabolic cost out."""

    #: Steepest gradient the model was fitted over, as rise/run.
    grade_limit: float = 0.45

    @abstractmethod
    def cost_j_per_kg_m(self, grade: float) -> float:
        """Net metabolic cost in joules per kg of body mass per metre travelled.

        ``grade`` is rise over run: ``+0.10`` climbs 10 m per 100 m travelled.
        Implementations must clamp to :attr:`grade_limit` rather than
        extrapolating, and must never return a value at or below zero — walking
        is never free, however steep the descent.
        """

    def clamp(self, grade: float) -> float:
        """Confine a gradient to the range the model was actually fitted over.

        DEM noise on a short edge routinely produces gradients of several
        hundred percent; extrapolating a fifth-order polynomial into that region
        produces spectacular nonsense, so every caller clamps first.
        """
        return max(-self.grade_limit, min(self.grade_limit, grade))

    def relative_to_flat(self, grade: float) -> float:
        """Cost at ``grade`` as a multiple of the cost on level ground.

        The router multiplies edge length by this, which keeps routing cost in
        recognisable "equivalent flat metres" and makes a flat route's cost equal
        its actual distance.
        """
        return self.cost_j_per_kg_m(grade) / self.cost_j_per_kg_m(0.0)


class MinettiCostModel(GradientCostModel):
    """Minetti et al. (2002), the fifth-order fit over -45% to +45%.

    This is the model the app uses. Its defining property, and the reason it was
    chosen over the far more common ACSM equation, is that it is *defined for
    descent*: cost falls below the level value on a gentle downhill, bottoms out
    near a 15% descent, then rises again on steeper ground as the legs switch
    from generating energy to absorbing it eccentrically.

    A note that has caught people out, including during this project's own
    development: the polynomial regresses the **average** cost across the tested
    speeds, giving 2.5 J/kg/m on the level. The paper separately reports a
    **minimum Cw** series — the cost at each gradient's speed-optimal point —
    which is 1.64 on the level and bottoms at -0.10. The two curves are both
    correct and describe different things. Average cost is right here, because
    people walk at their comfortable pace rather than at each hill's
    energetically optimal one.
    """

    #: Coefficients from highest power down: 280.5i^5 - 58.7i^4 - 76.8i^3
    #: + 51.9i^2 + 19.6i + 2.5, fitted with R^2 = 0.999.
    COEFFICIENTS = (280.5, -58.7, -76.8, 51.9, 19.6, 2.5)

    #: Floor applied to the polynomial's output. The fit is a regression, not a
    #: physical law, and dips fractionally low at the extremes of its range.
    MIN_COST = 0.3

    def cost_j_per_kg_m(self, grade: float) -> float:
        """Evaluate the polynomial at a clamped gradient, via Horner's method."""
        i = self.clamp(grade)
        a, b, c, d, e, f = self.COEFFICIENTS
        cost = ((((a * i + b) * i + c) * i + d) * i + e) * i + f
        return max(cost, self.MIN_COST)


class AcsmCostModel(GradientCostModel):
    """The ACSM walking equation, retained for comparison but not used.

    ``VO2 = 0.1*S + 1.8*S*G + 3.5`` (S in m/min, G fractional grade), valid for
    1.9-3.7 mph on **positive** grades only.

    It is here to be measured against, not to be used. Two documented problems
    rule it out for this app:

    1. The vertical term ``1.8*S*G`` is linear and unbounded below, so on a
       negative grade it predicts falling and then negative oxygen cost. In a
       city like San Francisco that means the downhill half of a loop refunds
       the calories of the uphill half.
    2. The ``3.5`` resting term is mL/kg/min of *total* body mass. Adipose
       tissue is far less metabolically active than muscle, so the equation is
       documented to overestimate energy expenditure substantially in people
       with obesity — precisely the users this product is most useful to.
    """

    #: The equation is only validated between these walking speeds, in m/min.
    VALID_SPEED_M_PER_MIN = (50.9, 99.2)

    def __init__(self, speed_m_per_min: float = 80.0):
        """Bind a walking speed, since ACSM's cost is speed-dependent.

        Minetti's cost of transport is per metre and speed-independent, so this
        adapter fixes a speed to make the two comparable at all.
        """
        self.speed_m_per_min = speed_m_per_min

    def vo2_ml_per_kg_min(self, grade: float) -> float:
        """Gross oxygen uptake predicted by the ACSM walking equation."""
        s = self.speed_m_per_min
        return 0.1 * s + 1.8 * s * grade + 3.5

    def cost_j_per_kg_m(self, grade: float) -> float:
        """Convert ACSM's gross VO2 into a per-metre cost, net of rest.

        Uses 20.9 kJ per litre of O2 (the standard caloric equivalent near a
        respiratory quotient of 0.9) and subtracts the resting term so the units
        line up with :class:`MinettiCostModel`.
        """
        net_vo2 = self.vo2_ml_per_kg_min(self.clamp(grade)) - 3.5
        joules_per_min_per_kg = net_vo2 / 1000.0 * 20_900.0
        return max(joules_per_min_per_kg / self.speed_m_per_min, self.MIN_COST)

    MIN_COST = 0.3


#: The application-wide default. Everything that needs a cost curve takes one by
#: injection and falls back to this, so a test can substitute another.
DEFAULT_COST_MODEL = MinettiCostModel()
