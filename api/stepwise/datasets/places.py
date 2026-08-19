"""Named destinations to aim a walk at.

"A loop out to Alta Plaza Park" is a walk someone wants to take; "a 2.1 km loop"
is a number. These come from Overture's places theme, filtered at build time to
the categories that make a walk worth taking and grouped so the API can explain
*why* a destination was picked.
"""

from __future__ import annotations

import logging
from typing import Any

from ..container import Container
from ..models.location import Coordinate, haversine

LOG = logging.getLogger(__name__)


class PlaceIndex:
    """Candidate walk destinations for one region."""

    def __init__(self, container: Container):
        """Bind a container and decode the place columns."""
        self.meta = container.meta
        self.names: list[str] = self.meta["names"]
        self.groups: list[str] = self.meta["groups"]
        self.categories: list[str] = self.meta["categories"]
        self.lat = container.get("place_lat")
        self.lon = container.get("place_lon")
        self.group = container.get("place_group")
        self.cat = container.get("place_cat")
        self.conf = container.get("place_conf")
        LOG.debug("PlaceIndex ready count=%d", len(self.lat))

    def __len__(self) -> int:
        """Number of destinations in this region."""
        return len(self.lat)

    def within(self, coordinate: Coordinate, radius_m: float) -> list[dict[str, Any]]:
        """Destinations inside a radius, nearest first.

        A linear scan is the right call at this size: after category filtering
        there are only a few thousand places per region, and scanning them is
        faster than maintaining and consulting a second spatial index. It would
        need revisiting at a hundred thousand.
        """
        found = []
        for i in range(len(self.lat)):
            distance = haversine(coordinate.lat, coordinate.lon, self.lat[i], self.lon[i])
            if distance <= radius_m:
                found.append(self.describe(i, distance))
        found.sort(key=lambda p: p["straight_line_m"])
        return found

    def describe(self, index: int, distance_m: float | None = None) -> dict[str, Any]:
        """Materialise one place into a dict."""
        record = {
            "index": index,
            "name": self.names[index],
            "category": self.categories[self.cat[index]],
            "group": self.groups[self.group[index]],
            "confidence": round(self.conf[index], 2),
            "lat": self.lat[index],
            "lon": self.lon[index],
        }
        if distance_m is not None:
            record["straight_line_m"] = round(distance_m)
        return record
