"""Runtime access to the baked walking graph.

Everything here is tuned for one constraint: this runs in a Lambda that has to
answer in a few hundred milliseconds, on a container that may have been created
by this very request. So there is no numpy, no per-request index building, and
no I/O beyond reading files that ship inside the deployment package.

The graph is held in CSR form (see :mod:`data.pipeline.build`). Neighbours of
node ``n`` are ``adj_edge[adj_start[n]:adj_start[n+1]]``; each entry packs an
edge index and a direction bit, so one integer says both "which edge" and "which
way am I walking it".

Datasets are cached at module scope, which is the whole trick to cold starts:
the first request in a container pays to decode the arrays, and every subsequent
request on that container reuses them for free.
"""

from __future__ import annotations

import json
import logging
import math
from array import array
from bisect import bisect_left
from pathlib import Path

from .container import Container

LOG = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
EARTH_R = 6371008.8

# Spatial index cell size in degrees of latitude (~111 m). Small enough that a
# nearest-node query inspects a handful of candidates, large enough that the
# index itself is cheap to build.
GRID_DEG = 0.001


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


class WalkGraph:
    """The routable network for one region."""

    def __init__(self, container: Container):
        self.c = container
        self.meta = container.meta
        self.n_nodes: int = self.meta["n_nodes"]
        self.n_edges: int = self.meta["n_edges"]
        self.names: list[str] = self.meta["names"]
        self.surfaces: list[str] = self.meta["surfaces"]

        self.node_lat = container.get("node_lat")
        self.node_lon = container.get("node_lon")
        self.node_ele_dm = container.get("node_ele_dm")
        self.adj_start = container.get("adj_start")
        self.adj_edge = container.get("adj_edge")
        self.edge_u = container.get("edge_u")
        self.edge_v = container.get("edge_v")
        self.edge_len = container.get("edge_len")
        self.edge_surface = container.get("edge_surface")
        self.edge_flags = container.get("edge_flags")
        self.edge_name = container.get("edge_name")

        self._grid: dict[tuple[int, int], list[int]] | None = None
        self._geom_start: array | None = None
        self._geom_lat: array | None = None
        self._geom_lon: array | None = None
        LOG.debug(
            "WalkGraph ready region=%s nodes=%d edges=%d",
            self.meta.get("region"),
            self.n_nodes,
            self.n_edges,
        )

    # --- geometry is decoded only when a route actually needs drawing -----

    @property
    def geom_start(self) -> array:
        if self._geom_start is None:
            self._geom_start = self.c.get("geom_start")
        return self._geom_start

    @property
    def geom_lat(self) -> array:
        if self._geom_lat is None:
            self._geom_lat = self.c.get("geom_lat")
        return self._geom_lat

    @property
    def geom_lon(self) -> array:
        if self._geom_lon is None:
            self._geom_lon = self.c.get("geom_lon")
        return self._geom_lon

    def edge_coords(self, edge: int, reverse: bool = False) -> list[list[float]]:
        """The polyline for one edge as ``[[lon, lat], ...]``, in travel order."""
        lo, hi = self.geom_start[edge], self.geom_start[edge + 1]
        lat, lon = self.geom_lat, self.geom_lon
        pts = [[lon[i], lat[i]] for i in range(lo, hi)]
        return pts[::-1] if reverse else pts

    def elevation(self, node: int) -> float:
        """Node elevation in metres (stored as decimetres)."""
        return self.node_ele_dm[node] / 10.0

    def edge_name_of(self, edge: int) -> str:
        return self.names[self.edge_name[edge]]

    def surface_of(self, edge: int) -> str:
        return self.surfaces[self.edge_surface[edge]]

    # --- spatial lookup ----------------------------------------------------

    def _build_grid(self) -> dict[tuple[int, int], list[int]]:
        grid: dict[tuple[int, int], list[int]] = {}
        lat, lon = self.node_lat, self.node_lon
        inv = 1.0 / GRID_DEG
        for i in range(self.n_nodes):
            key = (int(lat[i] * inv), int(lon[i] * inv))
            cell = grid.get(key)
            if cell is None:
                grid[key] = [i]
            else:
                cell.append(i)
        LOG.debug("spatial grid built cells=%d nodes=%d", len(grid), self.n_nodes)
        return grid

    @property
    def grid(self) -> dict[tuple[int, int], list[int]]:
        if self._grid is None:
            self._grid = self._build_grid()
        return self._grid

    def nearest_node(
        self, lat: float, lon: float, max_m: float = 500.0
    ) -> tuple[int, float] | None:
        """The closest graph node to a point, with its distance in metres.

        Searches outward ring by ring so a dense downtown query stops after one
        ring, while a point on the edge of coverage still finds something rather
        than scanning all 83,000 nodes.
        """
        inv = 1.0 / GRID_DEG
        clat, clon = int(lat * inv), int(lon * inv)
        grid = self.grid
        best: tuple[int, float] | None = None
        max_ring = max(1, int(math.ceil(max_m / 111_000.0 * inv)) + 1)

        hit_ring: int | None = None
        for ring in range(max_ring + 1):
            for dy in range(-ring, ring + 1):
                for dx in range(-ring, ring + 1):
                    # Only the perimeter of this ring; inner cells were done already.
                    if ring > 0 and abs(dy) != ring and abs(dx) != ring:
                        continue
                    for node in grid.get((clat + dy, clon + dx), ()):
                        d = haversine(lat, lon, self.node_lat[node], self.node_lon[node])
                        if d <= max_m and (best is None or d < best[1]):
                            best = (node, d)
                            if hit_ring is None:
                                hit_ring = ring
            # Search one ring past the first hit before stopping: a node just
            # over the cell boundary on the diagonal can be closer than the one
            # already found, and stopping immediately would snap to the wrong
            # side of the street.
            if hit_ring is not None and ring > hit_ring:
                break
        LOG.debug("nearest_node lat=%.5f lon=%.5f -> %s", lat, lon, best)
        return best


class AddressIndex:
    """Geocoder over the baked Overture address corpus."""

    def __init__(self, container: Container):
        self.c = container
        self.meta = container.meta
        self.streets: list[str] = self.meta["streets"]
        self.postcodes: list[str] = self.meta["postcodes"]
        self.ranges: dict[str, list[int]] = self.meta["street_ranges"]
        self.display: dict[str, str] = self.meta["street_display"]
        self.suffixes: dict[str, str] = self.meta["suffixes"]
        self.addr_num = container.get("addr_num")
        self.addr_lat = container.get("addr_lat")
        self.addr_lon = container.get("addr_lon")
        self.addr_street = container.get("addr_street")
        self.addr_post = container.get("addr_post")
        LOG.debug("AddressIndex ready count=%d streets=%d", len(self.addr_num), len(self.ranges))

    def lookup(self, street_norm: str, number: int) -> dict | None:
        """Exact or nearest house number on a known street.

        Nearest-number fallback is deliberate: address datasets always have
        holes, and putting someone at number 706 when they typed 708 is a much
        better answer than "address not found".
        """
        rng = self.ranges.get(street_norm)
        if rng is None:
            return None
        lo, hi = rng
        nums = self.addr_num
        # The slice is sorted by construction, so bisect finds the insertion
        # point and the answer is one of its two neighbours.
        pos = bisect_left(nums, number, lo, hi)
        candidates = [p for p in (pos - 1, pos, pos + 1) if lo <= p < hi]
        if not candidates:
            return None
        best = min(candidates, key=lambda p: abs(nums[p] - number))
        return self._row(best, exact=nums[best] == number)

    def _row(self, idx: int, exact: bool) -> dict:
        suffix = self.suffixes.get(str(idx), "")
        return {
            "number": f"{self.addr_num[idx]}{suffix}",
            "street": self.streets[self.addr_street[idx]],
            "postcode": self.postcodes[self.addr_post[idx]],
            "lat": self.addr_lat[idx],
            "lon": self.addr_lon[idx],
            "exact": exact,
        }

    def street_candidates(self, street_norm: str, limit: int = 5) -> list[str]:
        """Streets whose normalised name contains the query — for 'did you mean'."""
        if not street_norm:
            return []
        exact = [s for s in self.ranges if s == street_norm]
        prefix = [s for s in self.ranges if s.startswith(street_norm) and s not in exact]
        contains = [
            s for s in self.ranges if street_norm in s and s not in exact and s not in prefix
        ]
        out = (exact + sorted(prefix) + sorted(contains))[:limit]
        return [self.display.get(s, s) for s in out]


class PlaceIndex:
    """Candidate walk destinations, grouped by theme."""

    def __init__(self, container: Container):
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

    def within(self, lat: float, lon: float, radius_m: float) -> list[dict]:
        """Every destination inside a radius, nearest first.

        A linear scan is correct here: after category filtering there are only a
        few thousand places per region, and a scan of 4,000 haversines is faster
        than maintaining another spatial index.
        """
        out = []
        for i in range(len(self.lat)):
            d = haversine(lat, lon, self.lat[i], self.lon[i])
            if d <= radius_m:
                out.append(
                    {
                        "index": i,
                        "name": self.names[i],
                        "category": self.categories[self.cat[i]],
                        "group": self.groups[self.group[i]],
                        "confidence": round(self.conf[i], 2),
                        "lat": self.lat[i],
                        "lon": self.lon[i],
                        "straight_line_m": round(d),
                    }
                )
        out.sort(key=lambda p: p["straight_line_m"])
        return out


class GreenIndex:
    """Green-space centroids, used to score how leafy a route feels."""

    def __init__(self, container: Container):
        self.lat = container.get("green_lat")
        self.lon = container.get("green_lon")
        self.radius = container.get("green_radius")
        LOG.debug("GreenIndex ready count=%d", len(self.lat))

    def near(self, lat: float, lon: float, extra_m: float) -> bool:
        """Whether a point falls within any park's footprint plus a margin."""
        for i in range(len(self.lat)):
            if haversine(lat, lon, self.lat[i], self.lon[i]) <= self.radius[i] + extra_m:
                return True
        return False


class RegionData:
    """Everything the API needs for one region, loaded on demand."""

    def __init__(self, key: str, data_dir: Path = DATA_DIR):
        self.key = key
        self.dir = data_dir
        self._graph: WalkGraph | None = None
        self._addresses: AddressIndex | None = None
        self._places: PlaceIndex | None = None
        self._green: GreenIndex | None = None

    @property
    def graph(self) -> WalkGraph:
        if self._graph is None:
            self._graph = WalkGraph(Container.load(self.dir / f"{self.key}.graph.spw"))
        return self._graph

    @property
    def addresses(self) -> AddressIndex:
        if self._addresses is None:
            self._addresses = AddressIndex(Container.load(self.dir / f"{self.key}.addr.spw"))
        return self._addresses

    @property
    def places(self) -> PlaceIndex:
        if self._places is None:
            self._places = PlaceIndex(Container.load(self.dir / f"{self.key}.places.spw"))
        return self._places

    @property
    def green(self) -> GreenIndex:
        if self._green is None:
            self._green = GreenIndex(Container.load(self.dir / f"{self.key}.green.spw"))
        return self._green


_MANIFEST: dict | None = None
_REGIONS: dict[str, RegionData] = {}


def manifest(data_dir: Path = DATA_DIR) -> dict:
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = json.loads((data_dir / "manifest.json").read_text())
        LOG.info("manifest loaded regions=%s", [r["key"] for r in _MANIFEST["regions"]])
    return _MANIFEST


def region(key: str, data_dir: Path = DATA_DIR) -> RegionData:
    """Fetch (and cache for the life of the container) one region's data."""
    rd = _REGIONS.get(key)
    if rd is None:
        known = {r["key"] for r in manifest(data_dir)["regions"]}
        if key not in known:
            raise KeyError(f"unknown region {key!r}; known: {sorted(known)}")
        rd = RegionData(key, data_dir)
        _REGIONS[key] = rd
        LOG.info("region data registered key=%s", key)
    return rd


def region_for_point(lat: float, lon: float, data_dir: Path = DATA_DIR) -> str | None:
    """Which region's bounding box contains a point, if any."""
    for r in manifest(data_dir)["regions"]:
        w, s, e, n = r["bbox"]
        if w <= lon <= e and s <= lat <= n:
            return r["key"]
    return None
