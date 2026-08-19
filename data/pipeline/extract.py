"""Pull one region's slice of Overture Maps down to a local parquet cache.

Overture publishes the planet as GeoParquet on public S3. DuckDB can read it
directly and push the bounding-box filter down into the parquet row-group
statistics, so extracting one city is a matter of seconds and a few hundred MB
of scanning — no local planet dump required.

Nested Overture columns (``connectors``, ``road_flags``, ``access_restrictions``)
are converted to JSON text here rather than being unpacked in SQL. The shapes
are deeply nested and the rules for interpreting them are genuinely fiddly, so
they belong in readable Python (see :mod:`data.pipeline.build`) rather than in a
wall of ``list_transform``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from .config import OVERTURE_REGION, OVERTURE_S3, WALKABLE_CLASSES, Region

LOG = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"


def connect() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection wired for anonymous reads of Overture's S3 bucket."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"SET s3_region='{OVERTURE_REGION}';")
    # Overture's bucket is public; without this DuckDB would try (and fail) to
    # sign requests with whatever ambient AWS credentials happen to be present.
    con.execute("SET s3_access_key_id=''; SET s3_secret_access_key='';")
    con.execute("SET enable_progress_bar=false;")
    LOG.debug("duckdb connected version=%s region=%s", duckdb.__version__, OVERTURE_REGION)
    return con


def _source(theme: str, type_: str) -> str:
    return f"{OVERTURE_S3}/theme={theme}/type={type_}/*.parquet"


def _bbox_clause(region: Region, alias: str = "") -> str:
    """Bounding-box predicate against Overture's precomputed ``bbox`` struct.

    Testing ``bbox.xmin``/``bbox.ymin`` (rather than a spatial predicate on the
    geometry) is what lets DuckDB skip row groups without decoding geometry.
    A feature is kept if its *lower-left* corner is inside the window, which is
    the convention Overture's own examples use.
    """
    p = f"{alias}." if alias else ""
    w, s, e, n = region.bbox
    return (
        f"{p}bbox.xmin BETWEEN {w} AND {e} AND {p}bbox.ymin BETWEEN {s} AND {n}"
    )


def extract_segments(con: duckdb.DuckDBPyConnection, region: Region, out: Path) -> int:
    """Walkable road segments, with topology and surface attributes."""
    classes = ", ".join(f"'{c}'" for c in sorted(WALKABLE_CLASSES))
    sql = f"""
    COPY (
        SELECT
            id,
            class,
            subclass,
            names.primary                AS name,
            to_json(connectors)          AS connectors_json,
            to_json(road_surface)        AS road_surface_json,
            to_json(road_flags)          AS road_flags_json,
            to_json(access_restrictions) AS access_json,
            ST_AsGeoJSON(geometry)       AS geom_json
        FROM read_parquet('{_source("transportation", "segment")}', hive_partitioning=1)
        WHERE subtype = 'road'
          AND class IN ({classes})
          AND {_bbox_clause(region)}
    ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    con.execute(sql)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    LOG.info("extracted segments region=%s count=%d out=%s", region.key, n, out)
    return n


def extract_addresses(con: duckdb.DuckDBPyConnection, region: Region, out: Path) -> int:
    """Street addresses — this is the geocoder's entire corpus.

    Rows without a street or number cannot be matched by a user typing an
    address, so they are dropped rather than bloating the shipped index.
    """
    sql = f"""
    COPY (
        SELECT
            street,
            number,
            postcode,
            ST_X(geometry) AS lon,
            ST_Y(geometry) AS lat
        FROM read_parquet('{_source("addresses", "address")}', hive_partitioning=1)
        WHERE {_bbox_clause(region)}
          AND street IS NOT NULL AND number IS NOT NULL
    ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    con.execute(sql)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    LOG.info("extracted addresses region=%s count=%d out=%s", region.key, n, out)
    return n


def extract_places(con: duckdb.DuckDBPyConnection, region: Region, out: Path) -> int:
    """Named places — candidate destinations to aim a walk at.

    Overture ships a per-place ``confidence``; low-confidence rows are largely
    stale or duplicated business listings, so the bar is set at 0.5 to keep
    suggested destinations trustworthy.
    """
    sql = f"""
    COPY (
        SELECT
            names.primary        AS name,
            categories.primary   AS category,
            confidence,
            ST_X(geometry)       AS lon,
            ST_Y(geometry)       AS lat
        FROM read_parquet('{_source("places", "place")}', hive_partitioning=1)
        WHERE {_bbox_clause(region)}
          AND names.primary IS NOT NULL
          AND categories.primary IS NOT NULL
          AND confidence >= 0.5
    ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    con.execute(sql)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    LOG.info("extracted places region=%s count=%d out=%s", region.key, n, out)
    return n


def extract_green(con: duckdb.DuckDBPyConnection, region: Region, out: Path) -> int:
    """Green land-use polygons, reduced to centroid + area.

    The routing engine only needs "is there green space near this edge", so
    shipping full polygon rings would be a lot of bytes for no extra answer.
    A centroid plus an equivalent radius captures the question being asked.
    """
    from .config import GREEN_LANDUSE_SUBTYPES

    subtypes = ", ".join(f"'{s}'" for s in sorted(GREEN_LANDUSE_SUBTYPES))
    sql = f"""
    COPY (
        SELECT
            subtype,
            class,
            ST_X(ST_Centroid(geometry)) AS lon,
            ST_Y(ST_Centroid(geometry)) AS lat,
            ST_Area_Spheroid(geometry)  AS area_m2
        FROM read_parquet('{_source("base", "land_use")}', hive_partitioning=1)
        WHERE {_bbox_clause(region)}
          AND subtype IN ({subtypes})
          AND ST_Area_Spheroid(geometry) > 400
    ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """
    con.execute(sql)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    LOG.info("extracted green land-use region=%s count=%d out=%s", region.key, n, out)
    return n


EXTRACTORS = {
    "segments": extract_segments,
    "addresses": extract_addresses,
    "places": extract_places,
    "green": extract_green,
}


def extract_all(region: Region, cache_dir: Path = CACHE_DIR, force: bool = False) -> dict[str, Path]:
    """Run every extractor for one region, skipping any whose parquet is cached."""
    cache_dir = cache_dir / region.key
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: cache_dir / f"{name}.parquet" for name in EXTRACTORS}
    todo = {n: p for n, p in paths.items() if force or not p.exists()}
    if not todo:
        LOG.info("region=%s all extracts cached in %s (use --force to refetch)", region.key, cache_dir)
        return paths

    con = connect()
    try:
        for name, path in todo.items():
            LOG.info("extracting %s for region=%s -> %s", name, region.key, path)
            EXTRACTORS[name](con, region, path)
    finally:
        con.close()
    return paths
