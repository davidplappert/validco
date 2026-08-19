"""The routable walking network for one region.

Tuned for one constraint: this runs in a Lambda that must answer in a few
hundred milliseconds on a container the request may itself have created. So
there is no numpy, no per-request index building, and no I/O beyond files inside
the deployment package.

The graph is stored in CSR (compressed sparse row) form. Neighbours of node
``n`` are ``adj_edge[adj_start[n]:adj_start[n + 1]]`` — a contiguous slice — so
Dijkstra's inner loop is an integer range rather than a dict lookup per node.
Each entry packs the edge index and a direction bit, so one integer answers both
"which edge" and "which way am I walking it".
"""

from __future__ import annotations

import logging
import math
from array import array

from ..container import Container
from ..models.location import Coordinate, haversine

LOG = logging.getLogger(__name__)

#: Spatial index cell size in degrees (~111 m of latitude). Small enough that a
#: nearest-node query inspects a handful of candidates, large enough that
#: building the index stays cheap.
GRID_DEG = 0.001


class WalkGraph:
    """Nodes, edges and elevations for one region's walkable network."""

    def __init__(self, container: Container):
        """Bind a container and decode the columns routing always needs.

        Geometry columns are deliberately left alone — a request that only
        geocodes should never pay to materialise 437,000 coordinate pairs.
        """
        self.container = container
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

    # --- geometry, decoded only when a route needs drawing -----------------

    @property
    def geom_start(self) -> array:
        """CSR offsets into the geometry coordinate arrays, decoded on demand."""
        if self._geom_start is None:
            self._geom_start = self.container.get("geom_start")
        return self._geom_start

    @property
    def geom_lat(self) -> array:
        """Latitudes of every shape point, decoded on demand."""
        if self._geom_lat is None:
            self._geom_lat = self.container.get("geom_lat")
        return self._geom_lat

    @property
    def geom_lon(self) -> array:
        """Longitudes of every shape point, decoded on demand."""
        if self._geom_lon is None:
            self._geom_lon = self.container.get("geom_lon")
        return self._geom_lon

    def edge_coords(self, edge: int, reverse: bool = False) -> list[list[float]]:
        """One edge's polyline as ``[[lon, lat], ...]`` in travel order."""
        lo, hi = self.geom_start[edge], self.geom_start[edge + 1]
        lat, lon = self.geom_lat, self.geom_lon
        points = [[lon[i], lat[i]] for i in range(lo, hi)]
        return points[::-1] if reverse else points

    # --- node and edge attributes -----------------------------------------

    def elevation(self, node: int) -> float:
        """Node elevation in metres.

        Stored as int16 decimetres: 0.1 m resolution is already finer than the
        ~10 m DEM the values came from, and it halves the array.
        """
        return self.node_ele_dm[node] / 10.0

    def coordinate(self, node: int) -> Coordinate:
        """A node's position as a :class:`Coordinate`."""
        return Coordinate(self.node_lat[node], self.node_lon[node])

    def edge_name_of(self, edge: int) -> str:
        """Street name for an edge, resolved through the name dictionary."""
        return self.names[self.edge_name[edge]]

    def surface_of(self, edge: int) -> str:
        """Surface class for an edge as a string: path, sidewalk, crossing, road."""
        return self.surfaces[self.edge_surface[edge]]

    def neighbours(self, node: int) -> range:
        """Index range into ``adj_edge`` holding this node's half-edges."""
        return range(self.adj_start[node], self.adj_start[node + 1])

    @staticmethod
    def unpack(packed: int) -> tuple[int, bool]:
        """Split an adjacency entry into ``(edge_index, is_reversed)``."""
        return packed >> 1, bool(packed & 1)

    def head_of(self, edge: int, reverse: bool) -> int:
        """The node reached by traversing ``edge`` in the given direction."""
        return self.edge_u[edge] if reverse else self.edge_v[edge]

    # --- spatial lookup ----------------------------------------------------

    @property
    def grid(self) -> dict[tuple[int, int], list[int]]:
        """Lazily-built spatial hash from grid cell to node indices."""
        if self._grid is None:
            self._grid = self._build_grid()
        return self._grid

    def _build_grid(self) -> dict[tuple[int, int], list[int]]:
        """Bucket every node into a ~111 m cell.

        Roughly 80,000 dict operations, which costs tens of milliseconds once
        per container — cheaper than a single linear scan over the nodes, and it
        is reused by every subsequent request.
        """
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

    def nearest_node(self, coordinate: Coordinate, max_m: float = 500.0):
        """Closest node to a point, as ``(node, distance_m)``, or ``None``.

        Searches outward ring by ring, then continues for exactly one ring past
        the first hit. That extra ring is not optional: a node just over a cell
        boundary on the diagonal can be closer than the one already found, and
        stopping immediately would snap the walker to the wrong side of the
        street.
        """
        inv = 1.0 / GRID_DEG
        clat, clon = int(coordinate.lat * inv), int(coordinate.lon * inv)
        grid = self.grid
        best: tuple[int, float] | None = None
        hit_ring: int | None = None
        max_ring = max(1, int(math.ceil(max_m / 111_000.0 * inv)) + 1)

        for ring in range(max_ring + 1):
            for dy in range(-ring, ring + 1):
                for dx in range(-ring, ring + 1):
                    # Perimeter only; inner cells were covered by earlier rings.
                    if ring > 0 and abs(dy) != ring and abs(dx) != ring:
                        continue
                    for node in grid.get((clat + dy, clon + dx), ()):
                        d = haversine(
                            coordinate.lat,
                            coordinate.lon,
                            self.node_lat[node],
                            self.node_lon[node],
                        )
                        if d <= max_m and (best is None or d < best[1]):
                            best = (node, d)
                            if hit_ring is None:
                                hit_ring = ring
            if hit_ring is not None and ring > hit_ring:
                break

        LOG.debug("nearest_node lat=%.5f lon=%.5f -> %s", coordinate.lat, coordinate.lon, best)
        return best
