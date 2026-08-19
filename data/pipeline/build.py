"""Turn the cached Overture parquet into the binary artifacts the API ships.

The interesting work is here. Overture's transportation theme is a set of
*segments* — polylines with a list of *connectors* (shared nodes) positioned as
fractions along them. That is a topology description, not a routable graph, so
this module:

1. splits each segment at its connectors into edges between adjacent nodes,
2. classifies each edge as path / sidewalk / crossing / road, and flags stairs,
   busy arterials and unpaved surfaces,
3. drops anything pedestrians are not allowed on,
4. samples a DEM at every node so the router knows about hills,
5. keeps only the largest connected component, so no route can start on an
   island, and
6. packs the result into flat arrays via :mod:`stepwise.container`.

Addresses, destinations and green space are baked alongside it into their own
containers.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from array import array
from collections import defaultdict
from pathlib import Path

import duckdb

from .config import (
    BUSY_ROAD_CLASSES,
    DATASET_VERSION,
    DESTINATION_CATEGORIES,
    FLAG_BRIDGE,
    FLAG_BUSY,
    FLAG_INDOOR,
    FLAG_STEPS,
    FLAG_TUNNEL,
    FLAG_UNPAVED,
    SURFACE_CROSSING,
    SURFACE_PATH,
    SURFACE_ROAD,
    SURFACE_SIDEWALK,
    SURFACES,
    UNPAVED_SURFACES,
    Region,
)
from .elevation import TerrainSampler

# The container format lives with the runtime code so the writer and reader can
# never disagree about the layout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))
from stepwise.container import ContainerWriter  # noqa: E402
from stepwise.geocode import normalize_street  # noqa: E402

LOG = logging.getLogger(__name__)

EARTH_R = 6371008.8  # IUGG mean radius, metres


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(a))


# --- attribute interpretation ---------------------------------------------


def classify_surface(cls: str, subclass: str | None) -> int:
    """Map Overture class/subclass onto the four surfaces the product talks about.

    The distinction the user cares about is "am I walking on a road or on a
    footpath", so a footway tagged ``sidewalk`` is deliberately *not* the same
    thing as a park path, even though Overture gives both class ``footway``.
    """
    if cls == "footway":
        if subclass == "sidewalk":
            return SURFACE_SIDEWALK
        if subclass in ("crosswalk", "crossing"):
            return SURFACE_CROSSING
        return SURFACE_PATH
    if cls in ("path", "pedestrian", "steps", "track", "cycleway"):
        return SURFACE_PATH
    return SURFACE_ROAD


def parse_flags(cls: str, road_flags_json: str | None, surface_json: str | None) -> int:
    """Bit mask of the edge properties that affect routing and accessibility."""
    flags = 0
    if cls == "steps":
        flags |= FLAG_STEPS
    if cls in BUSY_ROAD_CLASSES:
        flags |= FLAG_BUSY

    for entry in _json_list(road_flags_json):
        for value in entry.get("values") or []:
            if value == "is_bridge":
                flags |= FLAG_BRIDGE
            elif value == "is_tunnel":
                flags |= FLAG_TUNNEL
            elif value == "is_covered":
                flags |= FLAG_INDOOR

    for entry in _json_list(surface_json):
        if (entry.get("value") or "") in UNPAVED_SURFACES:
            flags |= FLAG_UNPAVED
    return flags


def walkable_on_foot(access_json: str | None) -> bool:
    """Whether pedestrians may use this segment.

    Overture models access as a list of rules. We only honour *unconditional*
    denials that apply to walking (or to every mode); a rule limited to a time
    window, a heading, or another transport mode does not make the segment
    unwalkable in general. Being conservative in the other direction — dropping
    anything with any restriction at all — would delete most of downtown.
    """
    for rule in _json_list(access_json):
        if rule.get("access_type") != "denied":
            continue
        if rule.get("between"):
            continue  # applies to part of the segment only
        when = rule.get("when") or {}
        # A denial with no qualifiers at all bans everyone, walkers included.
        if not any(when.get(k) for k in ("during", "heading", "using", "recognized", "mode", "vehicle")):
            return False
        modes = when.get("mode") or []
        if "foot" in modes or "pedestrian" in modes:
            # ...unless it is also time-limited, which we do not model.
            if not when.get("during"):
                return False
    return True


def _json_list(raw: str | None) -> list[dict]:
    if not raw or raw in ("null", "[]"):
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(value, dict):
        return [value]
    return [v for v in (value or []) if isinstance(v, dict)]


# --- geometry --------------------------------------------------------------


def cumulative_lengths(coords: list[tuple[float, float]]) -> list[float]:
    """Running distance along a polyline of (lon, lat) pairs, in metres."""
    out = [0.0]
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i - 1]
        lon2, lat2 = coords[i]
        out.append(out[-1] + haversine(lat1, lon1, lat2, lon2))
    return out


def slice_polyline(
    coords: list[tuple[float, float]], cum: list[float], d0: float, d1: float
) -> list[tuple[float, float]]:
    """The sub-polyline between two distances along the line, with exact endpoints.

    Used to cut a segment at its connectors. Interpolating the end vertices
    (rather than snapping to the nearest existing vertex) matters because
    Overture places connectors mid-vertex all the time — snapping would leave
    visible gaps between consecutive edges when the route is drawn.
    """
    total = cum[-1]
    d0 = max(0.0, min(total, d0))
    d1 = max(0.0, min(total, d1))
    if d1 <= d0:
        return []

    def point_at(dist: float) -> tuple[float, float]:
        if dist <= 0:
            return coords[0]
        if dist >= total:
            return coords[-1]
        lo, hi = 0, len(cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if cum[mid] <= dist:
                lo = mid
            else:
                hi = mid
        span = cum[hi] - cum[lo]
        t = 0.0 if span <= 0 else (dist - cum[lo]) / span
        lon = coords[lo][0] + (coords[hi][0] - coords[lo][0]) * t
        lat = coords[lo][1] + (coords[hi][1] - coords[lo][1]) * t
        return (lon, lat)

    out = [point_at(d0)]
    for i, c in enumerate(cum):
        if d0 < c < d1:
            out.append(coords[i])
    out.append(point_at(d1))
    # Collapse duplicate consecutive points introduced by the interpolation.
    deduped = [out[0]]
    for pt in out[1:]:
        if abs(pt[0] - deduped[-1][0]) > 1e-9 or abs(pt[1] - deduped[-1][1]) > 1e-9:
            deduped.append(pt)
    return deduped if len(deduped) >= 2 else []


# --- graph assembly --------------------------------------------------------


class GraphBuilder:
    """Accumulates nodes and edges from split segments."""

    def __init__(self) -> None:
        self.node_ids: dict[str, int] = {}
        self.node_lon: list[float] = []
        self.node_lat: list[float] = []
        self.edges: list[tuple[int, int, float, int, int]] = []  # u, v, len, surface, flags
        self.edge_geom: list[list[tuple[float, float]]] = []
        self.edge_name: list[str] = []
        self.stats: dict[str, int] = defaultdict(int)

    def node(self, connector_id: str, lon: float, lat: float) -> int:
        idx = self.node_ids.get(connector_id)
        if idx is None:
            idx = len(self.node_lon)
            self.node_ids[connector_id] = idx
            self.node_lon.append(lon)
            self.node_lat.append(lat)
        return idx

    def add_segment(self, row: dict) -> None:
        raw_geom = json.loads(row["geom_json"]) if row["geom_json"] else None
        if not raw_geom or raw_geom.get("type") != "LineString":
            self.stats["skipped_not_linestring"] += 1
            return
        coords = [(float(c[0]), float(c[1])) for c in raw_geom["coordinates"]]
        if len(coords) < 2:
            self.stats["skipped_degenerate"] += 1
            return

        if not walkable_on_foot(row["access_json"]):
            self.stats["skipped_access_denied"] += 1
            return

        connectors = _json_list(row["connectors_json"])
        if len(connectors) < 2:
            # A segment with fewer than two connectors joins nothing; it cannot
            # participate in a route.
            self.stats["skipped_no_topology"] += 1
            return

        cum = cumulative_lengths(coords)
        total = cum[-1]
        if total <= 0.0:
            self.stats["skipped_zero_length"] += 1
            return

        surface = classify_surface(row["class"], row["subclass"])
        flags = parse_flags(row["class"], row["road_flags_json"], row["road_surface_json"])
        name = row["name"] or ""

        # Order connectors along the segment and materialise their positions.
        placed = []
        for conn in connectors:
            cid = conn.get("connector_id")
            at = conn.get("at")
            if cid is None or at is None:
                continue
            placed.append((float(at), cid))
        placed.sort()
        if len(placed) < 2:
            self.stats["skipped_no_topology"] += 1
            return

        for (at0, cid0), (at1, cid1) in zip(placed, placed[1:], strict=False):
            d0, d1 = at0 * total, at1 * total
            piece = slice_polyline(coords, cum, d0, d1)
            if not piece:
                self.stats["skipped_zero_edge"] += 1
                continue
            length = d1 - d0
            if length < 0.5:
                self.stats["skipped_tiny_edge"] += 1
                continue
            u = self.node(cid0, *piece[0])
            v = self.node(cid1, *piece[-1])
            if u == v:
                self.stats["skipped_self_loop"] += 1
                continue
            self.edges.append((u, v, length, surface, flags))
            self.edge_geom.append(piece)
            self.edge_name.append(name)
            self.stats[f"edges_{SURFACES[surface]}"] += 1

    def largest_component(self) -> set[int]:
        """Node indices in the biggest connected component.

        Overture's network contains genuinely disconnected fragments — a service
        road inside a fenced campus, a footpath whose connectors were never
        merged. Routing from one of those fails with no useful explanation, so
        they are dropped at build time instead.
        """
        adj: dict[int, list[int]] = defaultdict(list)
        for u, v, *_ in self.edges:
            adj[u].append(v)
            adj[v].append(u)

        seen: set[int] = set()
        best: set[int] = set()
        for start in range(len(self.node_lon)):
            if start in seen:
                continue
            stack = [start]
            comp = set()
            seen.add(start)
            while stack:
                n = stack.pop()
                comp.add(n)
                for m in adj[n]:
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
            if len(comp) > len(best):
                best = comp
        LOG.info(
            "connected components: kept %d of %d nodes (%.1f%%)",
            len(best), len(self.node_lon), 100.0 * len(best) / max(1, len(self.node_lon)),
        )
        return best


# --- packing ---------------------------------------------------------------


def pack_graph(builder: GraphBuilder, sampler: TerrainSampler, region: Region, out: Path) -> dict:
    """Write the routing graph as flat arrays in CSR (compressed sparse row) form.

    CSR is what makes the router fast without numpy: the neighbours of node ``n``
    are ``adj_edge[adj_start[n]:adj_start[n+1]]``, a contiguous slice, so the
    inner loop of Dijkstra is a list slice and an index rather than a dict lookup
    per node.
    """
    keep = builder.largest_component()

    # Renumber surviving nodes to a dense 0..N-1 range.
    remap: dict[int, int] = {}
    node_lat: list[float] = []
    node_lon: list[float] = []
    for old in sorted(keep):
        remap[old] = len(node_lat)
        node_lat.append(builder.node_lat[old])
        node_lon.append(builder.node_lon[old])

    LOG.info("sampling elevation for %d nodes", len(node_lat))
    node_ele_dm = array("h")  # decimetres: 0.1 m resolution is finer than the DEM
    for lat, lon in zip(node_lat, node_lon, strict=True):
        metres = sampler.sample(lat, lon)
        node_ele_dm.append(max(-32000, min(32000, int(round(metres * 10.0)))))
    LOG.info("elevation sampled misses=%d", sampler.misses)

    edge_u = array("I")
    edge_v = array("I")
    edge_len = array("f")
    edge_surface = array("B")
    edge_flags = array("B")
    geom_start = array("I", [0])
    geom_lat = array("f")
    geom_lon = array("f")
    name_ids = array("I")

    # Street names repeat heavily (every sidewalk edge on Market St shares one),
    # so they go in a dictionary and the edges store an index.
    name_dict: list[str] = [""]
    name_index: dict[str, int] = {"": 0}

    for (u, v, length, surface, flags), geom, name in zip(
        builder.edges, builder.edge_geom, builder.edge_name, strict=True
    ):
        if u not in remap or v not in remap:
            continue
        edge_u.append(remap[u])
        edge_v.append(remap[v])
        edge_len.append(length)
        edge_surface.append(surface)
        edge_flags.append(flags)
        for lon, lat in geom:
            geom_lon.append(lon)
            geom_lat.append(lat)
        geom_start.append(len(geom_lat))

        nid = name_index.get(name)
        if nid is None:
            nid = len(name_dict)
            name_index[name] = nid
            name_dict.append(name)
        name_ids.append(nid)

    n_nodes = len(node_lat)
    n_edges = len(edge_u)

    # Build CSR adjacency over directed half-edges. Each undirected edge appears
    # twice; the stored value is (edge_index << 1) | direction, so the router
    # recovers both which edge it is and which way it is traversing it.
    degree = array("I", bytes(4 * (n_nodes + 1)))
    for i in range(n_edges):
        degree[edge_u[i]] += 1
        degree[edge_v[i]] += 1
    adj_start = array("I", bytes(4 * (n_nodes + 1)))
    running = 0
    for n in range(n_nodes):
        adj_start[n] = running
        running += degree[n]
    adj_start[n_nodes] = running

    cursor = array("I", adj_start[:n_nodes])
    adj_edge = array("I", bytes(4 * running))
    for i in range(n_edges):
        u, v = edge_u[i], edge_v[i]
        adj_edge[cursor[u]] = (i << 1) | 0  # traversing u -> v
        cursor[u] += 1
        adj_edge[cursor[v]] = (i << 1) | 1  # traversing v -> u
        cursor[v] += 1

    writer = ContainerWriter()
    writer.add("node_lat", "f", node_lat)
    writer.add("node_lon", "f", node_lon)
    writer.add("node_ele_dm", "h", node_ele_dm)
    writer.add("adj_start", "I", adj_start)
    writer.add("adj_edge", "I", adj_edge)
    writer.add("edge_u", "I", edge_u)
    writer.add("edge_v", "I", edge_v)
    writer.add("edge_len", "f", edge_len)
    writer.add("edge_surface", "B", edge_surface)
    writer.add("edge_flags", "B", edge_flags)
    writer.add("edge_name", "I", name_ids)
    writer.add("geom_start", "I", geom_start)
    writer.add("geom_lat", "f", geom_lat)
    writer.add("geom_lon", "f", geom_lon)
    writer.meta = {
        "dataset_version": DATASET_VERSION,
        "region": region.key,
        "region_label": region.label,
        "bbox": list(region.bbox),
        "center": list(region.center),
        "n_nodes": n_nodes,
        "n_edges": n_edges,
        "names": name_dict,
        "surfaces": list(SURFACES),
        "build_stats": dict(builder.stats),
    }
    size = writer.write(out)
    LOG.info(
        "packed graph region=%s nodes=%d edges=%d geom_pts=%d bytes=%d",
        region.key, n_nodes, n_edges, len(geom_lat), size,
    )
    return {"n_nodes": n_nodes, "n_edges": n_edges, "bytes": size}


def pack_addresses(con: duckdb.DuckDBPyConnection, src: Path, region: Region, out: Path) -> dict:
    """Bake the geocoder index: a street dictionary plus rows sorted by street and number.

    Sorting is done on the *normalised* street name, not the raw one. Overture
    carries both "North Main Street" and, elsewhere, "N Main St"; those fold
    to the same key, and the range for that key has to be contiguous for the
    lookup to be a binary search. Sorting by the raw name would interleave them
    and silently lose half the house numbers on the street.

    The payoff at runtime: a lookup is one dict hit on the normalised street and
    a bisect on the number. Nothing proportional to the 400k-row corpus happens
    on a cold start.
    """
    rows = con.execute(
        f"SELECT street, number, postcode, lon, lat FROM read_parquet('{src}')"
    ).fetchall()

    # Normalise and sort in Python so the ordering matches the runtime's own
    # notion of street identity exactly.
    prepared: list[tuple[str, int, str, str, str, float, float]] = []
    for street, number, postcode, lon, lat in rows:
        street = (street or "").strip()
        number = (number or "").strip()
        if not street or not number:
            continue
        digits = "".join(ch for ch in number if ch.isdigit())
        if not digits:
            continue
        numeric = int(digits[:9])
        suffix = number[len(digits):].strip() if number.startswith(digits) else ""
        prepared.append(
            (normalize_street(street), numeric, suffix, street, postcode or "", float(lon), float(lat))
        )
    prepared.sort(key=lambda r: (r[0], r[1], r[2]))

    street_dict: list[str] = []
    street_index: dict[str, int] = {}
    postcode_dict: list[str] = [""]
    postcode_index: dict[str, int] = {"": 0}
    street_ranges: dict[str, list[int]] = {}
    street_display: dict[str, str] = {}
    suffixes: dict[str, str] = {}

    addr_street = array("I")
    addr_num = array("I")
    addr_lat = array("f")
    addr_lon = array("f")
    addr_post = array("H")

    for norm, numeric, suffix, display, postcode, lon, lat in prepared:
        sid = street_index.get(display)
        if sid is None:
            sid = len(street_dict)
            street_index[display] = sid
            street_dict.append(display)

        pid = postcode_index.get(postcode)
        if pid is None:
            pid = len(postcode_dict)
            postcode_index[postcode] = pid
            postcode_dict.append(postcode)

        idx = len(addr_num)
        if suffix:
            suffixes[str(idx)] = suffix

        addr_street.append(sid)
        addr_num.append(numeric)
        addr_lat.append(lat)
        addr_lon.append(lon)
        addr_post.append(pid)

        rng = street_ranges.get(norm)
        if rng is None:
            street_ranges[norm] = [idx, idx + 1]
            street_display[norm] = display
        else:
            rng[1] = idx + 1

    writer = ContainerWriter()
    writer.add("addr_street", "I", addr_street)
    writer.add("addr_num", "I", addr_num)
    writer.add("addr_lat", "f", addr_lat)
    writer.add("addr_lon", "f", addr_lon)
    writer.add("addr_post", "H", addr_post)
    writer.meta = {
        "dataset_version": DATASET_VERSION,
        "region": region.key,
        "streets": street_dict,
        "postcodes": postcode_dict,
        "street_ranges": street_ranges,
        "street_display": street_display,
        "suffixes": suffixes,
        "count": len(addr_num),
    }
    size = writer.write(out)
    LOG.info(
        "packed addresses region=%s count=%d streets=%d normalised=%d bytes=%d",
        region.key, len(addr_num), len(street_dict), len(street_ranges), size,
    )
    return {"count": len(addr_num), "streets": len(street_dict), "bytes": size}


def pack_places(con: duckdb.DuckDBPyConnection, src: Path, region: Region, out: Path) -> dict:
    """Bake walk destinations, grouped into the themes the API explains routes with."""
    cat_to_group = {
        cat: group for group, cats in DESTINATION_CATEGORIES.items() for cat in cats
    }
    wanted = ", ".join(f"'{c}'" for c in sorted(cat_to_group))
    rows = con.execute(
        f"""
        SELECT name, category, confidence, lon, lat
        FROM read_parquet('{src}')
        WHERE category IN ({wanted})
        ORDER BY confidence DESC
        """
    ).fetchall()

    groups = list(DESTINATION_CATEGORIES)
    place_lat = array("f")
    place_lon = array("f")
    place_group = array("B")
    place_conf = array("f")
    names: list[str] = []
    categories: list[str] = []
    cat_dict: list[str] = []
    cat_index: dict[str, int] = {}
    place_cat = array("H")

    for name, category, confidence, lon, lat in rows:
        group = cat_to_group.get(category)
        if group is None:
            continue
        cid = cat_index.get(category)
        if cid is None:
            cid = len(cat_dict)
            cat_index[category] = cid
            cat_dict.append(category)
        place_lat.append(float(lat))
        place_lon.append(float(lon))
        place_group.append(groups.index(group))
        place_conf.append(float(confidence or 0.0))
        place_cat.append(cid)
        names.append(name)

    writer = ContainerWriter()
    writer.add("place_lat", "f", place_lat)
    writer.add("place_lon", "f", place_lon)
    writer.add("place_group", "B", place_group)
    writer.add("place_cat", "H", place_cat)
    writer.add("place_conf", "f", place_conf)
    writer.meta = {
        "dataset_version": DATASET_VERSION,
        "region": region.key,
        "groups": groups,
        "categories": cat_dict,
        "names": names,
        "count": len(place_lat),
    }
    size = writer.write(out)
    LOG.info("packed places region=%s count=%d bytes=%d", region.key, len(place_lat), size)
    return {"count": len(place_lat), "bytes": size}


def pack_green(con: duckdb.DuckDBPyConnection, src: Path, region: Region, out: Path) -> dict:
    """Bake green-space centroids and their equivalent radii."""
    rows = con.execute(
        f"SELECT subtype, class, lon, lat, area_m2 FROM read_parquet('{src}') ORDER BY area_m2 DESC"
    ).fetchall()

    green_lat = array("f")
    green_lon = array("f")
    green_radius = array("f")
    for _subtype, _cls, lon, lat, area in rows:
        green_lat.append(float(lat))
        green_lon.append(float(lon))
        # Radius of a circle with the same area — a reasonable stand-in for
        # "how far does this park's influence reach".
        green_radius.append(math.sqrt(max(0.0, float(area)) / math.pi))

    writer = ContainerWriter()
    writer.add("green_lat", "f", green_lat)
    writer.add("green_lon", "f", green_lon)
    writer.add("green_radius", "f", green_radius)
    writer.meta = {"dataset_version": DATASET_VERSION, "region": region.key, "count": len(green_lat)}
    size = writer.write(out)
    LOG.info("packed green region=%s count=%d bytes=%d", region.key, len(green_lat), size)
    return {"count": len(green_lat), "bytes": size}
