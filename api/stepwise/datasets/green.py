"""Green space, reduced to what the routing question actually needs.

The engine only ever asks "is there park-like land near this point", so the
build ships polygon centroids and an equivalent radius rather than full rings.
Shipping the geometry would be a lot of bytes for an answer nothing consumes.
"""

from __future__ import annotations

import logging

from ..container import Container
from ..models.location import Coordinate, haversine

LOG = logging.getLogger(__name__)


class GreenIndex:
    """Green-space proximity lookup for one region."""

    def __init__(self, container: Container):
        """Bind a container and decode the green-space columns."""
        self.lat = container.get("green_lat")
        self.lon = container.get("green_lon")
        self.radius = container.get("green_radius")
        LOG.debug("GreenIndex ready count=%d", len(self.lat))

    def __len__(self) -> int:
        """Number of green-space features in this region."""
        return len(self.lat)

    def near(self, coordinate: Coordinate, margin_m: float = 120.0) -> bool:
        """Whether a point falls within any green feature's footprint plus a margin.

        The margin is generous on purpose: the question is "does this walk feel
        green", not a precise containment test, and a path running along a
        park's edge counts.
        """
        for i in range(len(self.lat)):
            distance = haversine(coordinate.lat, coordinate.lon, self.lat[i], self.lon[i])
            if distance <= self.radius[i] + margin_m:
                return True
        return False
