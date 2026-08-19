"""The region builder Lambda.

Invoked asynchronously when someone asks for a city that is not yet covered. It
resolves the place, runs the same extraction pipeline that produced the bundled
regions, uploads the baked containers to S3, and reports progress throughout so
the browser can show a real progress bar rather than a spinner.

Why this is a separate function
-------------------------------
It has an entirely different shape from the API. It needs DuckDB and Pillow
(tens of megabytes), several minutes of runtime, and a gigabyte of memory. The
request-path Lambda needs none of that and would inherit a far worse cold start
if the two shared a package. Splitting them keeps a plan request answering in
milliseconds while an extraction takes as long as it takes.

Progress weighting
------------------
The reported fractions are not evenly spaced; they are roughly proportional to
the wall time each stage actually takes, so the bar moves at a steady pace. The
terrain download dominates for a large city, which is why it owns the widest
band.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
import traceback
from pathlib import Path

# The bundle places the runtime package and the pipeline alongside this module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from place import PlaceResolver  # noqa: E402
from stepwise.datasets.catalog import RegionCatalog, RegionStatus, clamp_bbox  # noqa: E402
from stepwise.logging_config import configure  # noqa: E402

from data.pipeline import build as pipeline_build  # noqa: E402
from data.pipeline import extract as pipeline_extract  # noqa: E402
from data.pipeline.config import OVERTURE_S3, Region  # noqa: E402
from data.pipeline.elevation import TerrainSampler  # noqa: E402

configure()
LOG = logging.getLogger("stepwise.builder")

#: Stage boundaries as fractions of the whole build, weighted by observed cost.
STAGES = {
    "resolve": (0.02, "Finding the area"),
    "segments": (0.20, "Downloading the walking network"),
    "addresses": (0.32, "Downloading addresses"),
    "places": (0.40, "Downloading destinations"),
    "green": (0.45, "Downloading green space"),
    "graph": (0.55, "Building the routable graph"),
    "terrain": (0.80, "Sampling elevation"),
    "pack": (0.92, "Packing the datasets"),
    "upload": (0.99, "Publishing"),
}


class RegionBuilder:
    """Builds one region end to end, reporting progress as it goes."""

    def __init__(self, catalog: RegionCatalog, status: RegionStatus):
        """Bind the catalogue to report into and the region being built."""
        self.catalog = catalog
        self.status = status
        self.workdir = Path(tempfile.mkdtemp(prefix=f"build-{status.key}-"))

    def report(self, stage: str, detail: str = "") -> None:
        """Advance the progress bar to a named stage."""
        fraction, message = STAGES[stage]
        self.catalog.progress(self.status, fraction, stage, detail or message)

    def resolve(self, con, payload: dict) -> dict:
        """Turn the request into a label and a buildable bounding box."""
        self.report("resolve")
        resolver = PlaceResolver(con, OVERTURE_S3)

        if payload.get("place"):
            found = resolver.resolve_name(payload["place"])
        else:
            found = resolver.resolve_point(float(payload["lat"]), float(payload["lon"]))

        if found is None:
            raise ValueError(
                f"could not find {payload.get('place') or 'that location'} in Overture's "
                "division data. Try a nearby city or town name."
            )

        # Clamp so a request for a whole country cannot time the builder out,
        # and a request for a hamlet still yields a walkable network.
        found["bbox"] = clamp_bbox(found["bbox"])
        LOG.info(
            "resolved region key=%s label=%s bbox=%s",
            self.status.key,
            found["label"],
            found["bbox"],
        )
        return found

    def extract(self, con, region: Region) -> dict[str, Path]:
        """Pull this region's slice of each Overture theme."""
        paths: dict[str, Path] = {}
        for name, stage in (
            ("segments", "segments"),
            ("addresses", "addresses"),
            ("places", "places"),
            ("green", "green"),
        ):
            self.report(stage)
            destination = self.workdir / f"{name}.parquet"
            pipeline_extract.EXTRACTORS[name](con, region, destination)
            paths[name] = destination
        return paths

    def assemble(self, con, region: Region, paths: dict[str, Path]) -> dict:
        """Build the graph, sample elevation, and pack every container."""
        self.report("graph")
        builder = pipeline_build.GraphBuilder()
        rows = con.execute(
            f"""
            SELECT id, class, subclass, name, connectors_json, road_surface_json,
                   road_flags_json, access_json, geom_json
            FROM read_parquet('{paths["segments"]}')
            """
        )
        columns = [d[0] for d in rows.description]
        while True:
            batch = rows.fetchmany(5000)
            if not batch:
                break
            for record in batch:
                builder.add_segment(dict(zip(columns, record, strict=True)))

        if not builder.edges:
            raise ValueError(
                "no walkable streets or paths were found in that area — "
                "Overture may not cover it in detail yet"
            )

        self.report("terrain")
        # Terrain tiles are cached under /tmp, which persists for the life of
        # the container: two nearby cities built back to back share downloads.
        sampler = TerrainSampler(cache_dir=Path(tempfile.gettempdir()) / "terrain")
        sampler.preload(region.bbox)

        self.report("pack")
        stats = {
            "graph": pipeline_build.pack_graph(
                builder, sampler, region, self.workdir / f"{region.key}.graph.spw"
            ),
            "addresses": pipeline_build.pack_addresses(
                con, paths["addresses"], region, self.workdir / f"{region.key}.addr.spw"
            ),
            "places": pipeline_build.pack_places(
                con, paths["places"], region, self.workdir / f"{region.key}.places.spw"
            ),
            "green": pipeline_build.pack_green(
                con, paths["green"], region, self.workdir / f"{region.key}.green.spw"
            ),
        }
        return stats

    def publish(self, region: Region) -> None:
        """Upload the baked containers so every container can serve them."""
        self.report("upload")
        for suffix in ("graph", "addr", "places", "green"):
            self.catalog.upload(
                region.key, suffix, str(self.workdir / f"{region.key}.{suffix}.spw")
            )

    def cleanup(self) -> None:
        """Remove the working directory.

        Lambda's /tmp is reused across invocations on a warm container, so a
        build that left its parquet behind would eventually fill it.
        """
        import shutil

        shutil.rmtree(self.workdir, ignore_errors=True)


def handler(event: dict, context=None) -> dict:
    """Build one region. Invoked asynchronously; nothing awaits the result."""
    started = time.monotonic()
    key = event["key"]
    catalog = RegionCatalog()

    # Checked before anything expensive happens. With no bucket the catalogue
    # degrades to a no-op — progress writes vanish, the completion write
    # vanishes — so the build would run for a minute or two and then die at the
    # upload with nothing recorded anywhere the user can see. Better to say so
    # in the logs immediately and cost nothing.
    if not catalog.enabled:
        LOG.error("region build cannot start key=%s: REGION_BUCKET is not set", key)
        raise RuntimeError(
            "REGION_BUCKET is not configured on the builder function; "
            "there is nowhere to record progress or publish the datasets"
        )

    status = catalog.get(key) or RegionStatus(key=key, label=event.get("label", key))
    LOG.info("region build starting key=%s payload=%s", key, event)

    builder = RegionBuilder(catalog, status)
    con = None
    try:
        con = pipeline_extract.connect()
        # DuckDB downloads its extensions on first use; /tmp is the only
        # writable path in Lambda.
        con.execute(f"SET extension_directory='{tempfile.gettempdir()}/duckdb-extensions';")

        found = builder.resolve(con, event)
        status.label = found["label"]
        status.bbox = found["bbox"]
        west, south, east, north = found["bbox"]
        status.center = [(south + north) / 2, (west + east) / 2]

        region = Region(
            key=key,
            label=found["label"],
            bbox=(west, south, east, north),
            center=(status.center[0], status.center[1]),
        )

        paths = builder.extract(con, region)
        stats = builder.assemble(con, region, paths)
        builder.publish(region)

        elapsed = round(time.monotonic() - started, 1)
        catalog.complete(
            status,
            {
                "n_nodes": stats["graph"]["n_nodes"],
                "n_edges": stats["graph"]["n_edges"],
                "n_addresses": stats["addresses"]["count"],
                "n_places": stats["places"]["count"],
                "bytes": sum(s["bytes"] for s in stats.values()),
                "build_seconds": elapsed,
            },
        )
        LOG.info("region build finished key=%s seconds=%.1f", key, elapsed)
        return {"ok": True, "key": key, "seconds": elapsed}

    except Exception as exc:  # noqa: BLE001 — the failure must reach the user
        LOG.error(
            "region build failed key=%s error=%s",
            key,
            exc,
            extra={"traceback": traceback.format_exc()},
        )
        catalog.fail(status, str(exc))
        return {"ok": False, "key": key, "error": str(exc)}
    finally:
        if con is not None:
            con.close()
        builder.cleanup()
