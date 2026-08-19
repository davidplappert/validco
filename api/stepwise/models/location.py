"""Points on the earth, and the results of trying to find one.

Small models, but they stop coordinates being passed around as bare tuples,
which is how a lat/lon pair ends up swapped. GeoJSON orders longitude first and
almost everything else orders latitude first; naming both ends that argument.
"""

from __future__ import annotations

import logging
import math
from typing import Any, NamedTuple

LOG = logging.getLogger(__name__)

#: IUGG mean earth radius, metres.
EARTH_RADIUS_M = 6371008.8


class Coordinate(NamedTuple):
    """A latitude/longitude pair, always in that order."""

    lat: float
    lon: float

    def distance_to(self, other: Coordinate) -> float:
        """Great-circle distance to another coordinate, in metres."""
        return haversine(self.lat, self.lon, other.lat, other.lon)

    def bearing_to(self, other: Coordinate) -> float:
        """Initial compass bearing toward another coordinate, in degrees."""
        p1, p2 = math.radians(self.lat), math.radians(other.lat)
        dl = math.radians(other.lon - self.lon)
        y = math.sin(dl) * math.cos(p2)
        x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    def to_geojson(self) -> list[float]:
        """As a GeoJSON position, which is ``[lon, lat]``."""
        return [self.lon, self.lat]

    def to_dict(self) -> dict[str, float]:
        """Serialise for the API response."""
        return {"lat": self.lat, "lon": self.lon}


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres.

    A module-level function as well as a method because the routing hot loops
    call it millions of times and want it without constructing objects.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def compass(bearing: float) -> str:
    """Eight-point compass label for a bearing in degrees."""
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((bearing + 22.5) // 45) % 8]


class GeocodeResult:
    """The outcome of resolving free text to a point.

    Models success and failure in one type so callers branch on ``found``
    instead of on ``None``, and so a failure can still carry the suggestions
    that make it actionable.
    """

    def __init__(
        self,
        found: bool,
        *,
        coordinate: Coordinate | None = None,
        label: str = "",
        match: str = "",
        address: dict[str, Any] | None = None,
        reason: str = "",
        suggestions: list[str] | None = None,
        region: str | None = None,
    ):
        """Construct either a hit or a miss; prefer the two classmethods."""
        self.found = found
        self.coordinate = coordinate
        self.label = label
        self.match = match
        self.address = address
        self.reason = reason
        self.suggestions = suggestions or []
        self.region = region

    @classmethod
    def hit(
        cls,
        coordinate: Coordinate,
        label: str,
        match: str,
        address: dict[str, Any] | None = None,
    ) -> GeocodeResult:
        """A successful match.

        ``match`` records *how* it matched — ``exact``, ``nearest_number``,
        ``street_midpoint`` or ``coordinates`` — because "we put you 40 m away
        because that house number is not in the data" is worth telling the user.
        """
        return cls(True, coordinate=coordinate, label=label, match=match, address=address)

    @classmethod
    def miss(cls, reason: str, suggestions: list[str] | None = None) -> GeocodeResult:
        """A failure, carrying the reason and any near-miss street names."""
        return cls(False, reason=reason, suggestions=suggestions)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        if not self.found:
            return {
                "found": False,
                "reason": self.reason,
                "suggestions": self.suggestions,
                **({"region": self.region} if self.region else {}),
            }
        return {
            "found": True,
            "match": self.match,
            "lat": self.coordinate.lat,
            "lon": self.coordinate.lon,
            "label": self.label,
            **({"address": self.address} if self.address else {}),
            **({"region": self.region} if self.region else {}),
        }


class Origin:
    """Where a walk starts: what the user asked for, and where it snapped to.

    Both are reported. The snap distance is how the app admits that it put you
    on the nearest mapped path rather than on your doorstep — which in
    Morton can be 40 m away.
    """

    def __init__(
        self,
        requested: Coordinate,
        snapped: Coordinate,
        snap_distance_m: float,
        geocode: GeocodeResult | None = None,
    ):
        """Bind the requested point, the graph node it snapped to, and the gap."""
        self.requested = requested
        self.snapped = snapped
        self.snap_distance_m = snap_distance_m
        self.geocode = geocode

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        payload: dict[str, Any] = {
            "lat": self.requested.lat,
            "lon": self.requested.lon,
            "snapped_lat": self.snapped.lat,
            "snapped_lon": self.snapped.lon,
            "snap_distance_m": round(self.snap_distance_m),
        }
        if self.geocode:
            payload["label"] = self.geocode.label
            payload["match"] = self.geocode.match
            if self.geocode.address:
                payload["address"] = self.geocode.address
        return payload
