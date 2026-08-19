"""Coverage areas.

A region is one buildable city-sized window with its own baked datasets. The
model exists so "is this point covered" and "which regions are there" are
answered by an object rather than by dictionary lookups scattered across the
controllers.
"""

from __future__ import annotations

import logging
from typing import Any

from .location import Coordinate

LOG = logging.getLogger(__name__)


class BoundingBox:
    """A west/south/east/north window in degrees."""

    __slots__ = ("west", "south", "east", "north")

    def __init__(self, west: float, south: float, east: float, north: float):
        """Store the four edges."""
        self.west, self.south, self.east, self.north = west, south, east, north

    @classmethod
    def from_list(cls, values: list[float]) -> BoundingBox:
        """Build from the ``[w, s, e, n]`` list stored in the manifest."""
        return cls(*values)

    def contains(self, coordinate: Coordinate) -> bool:
        """Whether a coordinate falls inside this window."""
        return (
            self.west <= coordinate.lon <= self.east and self.south <= coordinate.lat <= self.north
        )

    def to_list(self) -> list[float]:
        """As the ``[w, s, e, n]`` list the API and frontend use."""
        return [self.west, self.south, self.east, self.north]


class Region:
    """One coverage area and the size of its datasets."""

    __slots__ = ("key", "label", "center", "bbox", "n_nodes", "n_edges", "n_addresses", "n_places")

    def __init__(self, **fields: Any):
        """Build from a manifest entry."""
        self.key: str = fields["key"]
        self.label: str = fields["label"]
        self.center = Coordinate(*fields["center"])
        self.bbox = BoundingBox.from_list(fields["bbox"])
        self.n_nodes: int = fields.get("n_nodes", 0)
        self.n_edges: int = fields.get("n_edges", 0)
        self.n_addresses: int = fields.get("n_addresses", 0)
        self.n_places: int = fields.get("n_places", 0)

    def contains(self, coordinate: Coordinate) -> bool:
        """Whether this region covers a coordinate."""
        return self.bbox.contains(coordinate)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "key": self.key,
            "label": self.label,
            "center": [self.center.lat, self.center.lon],
            "bbox": self.bbox.to_list(),
            "n_nodes": self.n_nodes,
            "n_edges": self.n_edges,
            "n_addresses": self.n_addresses,
            "n_places": self.n_places,
        }

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return f"Region({self.key!r}, {self.label!r}, nodes={self.n_nodes})"
