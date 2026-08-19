"""Ranking candidate walks.

Split from the planner because it is pure policy: given routes that already
know their own effort and suitability, decide which to show and in what order.
Keeping it separate means the ranking can be tested with hand-built routes and
no graph search at all.

The dominant term is closeness to the requested duration. A beautiful
seventy-minute route is the wrong answer to "I have thirty minutes", and an
earlier version of this app that ranked on scenery learned that the hard way.
"""

from __future__ import annotations

import logging
import math

from ..models.route import Route
from .search import Preferences

LOG = logging.getLogger(__name__)


class ScoringWeights:
    """The knobs that decide which walk is best.

    Gathered into one object so the ranking policy can be adjusted, or varied in
    a test, without hunting for magic numbers inside the loop.
    """

    #: How sharply the score falls away from the requested duration. Higher is
    #: less forgiving of a route that runs long.
    DURATION_DECAY = 3.0

    #: Split between "is this the length I asked for" and "does it suit me".
    DURATION_SHARE = 0.6
    SUITABILITY_SHARE = 0.4

    #: Points per percent of the route spent on dedicated walking paths.
    PATH_BONUS_PER_PCT = 0.12

    #: Flat bonuses.
    GREEN_BONUS = 8.0
    NAMED_DESTINATION_BONUS = 6.0
    LOOP_BONUS = 5.0


class RouteScorer:
    """Scores and orders candidate routes for one request."""

    def __init__(self, weights: ScoringWeights | None = None, green_check=None):
        """Bind the weights and an optional "is this route green" predicate."""
        self.weights = weights or ScoringWeights()
        self.green_check = green_check

    def score(self, route: Route, target_minutes: float, preferences: Preferences) -> float:
        """Score one route out of roughly 100, higher being better."""
        w = self.weights
        minutes = route.effort.duration_min

        # Exponential rather than linear so being a few minutes out barely
        # matters and being double the request is disqualifying.
        error = abs(minutes - target_minutes) / max(1.0, target_minutes)
        duration_score = 100.0 * math.exp(-w.DURATION_DECAY * error)

        suitability = route.suitability.score if route.suitability else 50.0
        total = w.DURATION_SHARE * duration_score + w.SUITABILITY_SHARE * suitability

        if preferences.prefer_paths:
            total += route.surfaces.share_of("path") * w.PATH_BONUS_PER_PCT
        if preferences.prefer_green and self.green_check and self.green_check(route):
            total += w.GREEN_BONUS
        if route.destination is not None:
            total += w.NAMED_DESTINATION_BONUS
        if route.shape == "loop":
            total += w.LOOP_BONUS

        return total

    def rank(
        self,
        routes: list[Route],
        target_minutes: float,
        preferences: Preferences,
        limit: int = 4,
    ) -> list[Route]:
        """Score, sort and de-duplicate candidates down to ``limit``."""
        for route in routes:
            route.score = self.score(route, target_minutes, preferences)
        routes.sort(key=lambda r: -r.score)
        chosen = self.diversify(routes, limit)
        LOG.info(
            "ranked candidates=%d returned=%d top=%.1f",
            len(routes),
            len(chosen),
            chosen[0].score if chosen else 0.0,
        )
        return chosen

    @staticmethod
    def diversify(routes: list[Route], limit: int) -> list[Route]:
        """Keep the best routes that are meaningfully different from each other.

        Without this the top four suggestions are frequently the same walk with
        one block varied, which reads as the app having nothing to offer.
        """
        from ..models.route import Route as RouteType  # local import: type only

        chosen: list[RouteType] = []
        for route in routes:
            if any(route.overlap_with(other) > 0.6 for other in chosen):
                continue
            chosen.append(route)
            if len(chosen) >= limit:
                break
        return chosen
