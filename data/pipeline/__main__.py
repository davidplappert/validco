"""CLI for the StepWise dataset build.

    python -m data.pipeline build            # every region
    python -m data.pipeline build --region sf
    python -m data.pipeline extract --force  # refetch the Overture cache

The build is offline and idempotent. Artifacts land in ``api/stepwise/data/``
so they are bundled straight into the Lambda package — no runtime S3 fetch, no
cold-start download, nothing to keep in sync at deploy time.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .build import GraphBuilder, pack_addresses, pack_graph, pack_green, pack_places
from .config import DEFAULT_REGION, REGIONS, Region
from .elevation import TerrainSampler
from .extract import CACHE_DIR, connect, extract_all

LOG = logging.getLogger("stepwise.build")

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "api" / "stepwise" / "data"


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stderr,
    )
    # DuckDB and urllib3 are extremely chatty at DEBUG and drown out our own log.
    logging.getLogger("urllib3").setLevel(logging.INFO)


def build_region(region: Region, cache_dir: Path, out_dir: Path, force: bool) -> dict:
    started = time.monotonic()
    LOG.info("=== building region %s (%s) ===", region.key, region.label)
    paths = extract_all(region, cache_dir=cache_dir, force=force)
    out_dir.mkdir(parents=True, exist_ok=True)

    con = connect()
    try:
        LOG.info("assembling walk graph from %s", paths["segments"])
        builder = GraphBuilder()
        rows = con.execute(
            f"""
            SELECT id, class, subclass, name, connectors_json, road_surface_json,
                   road_flags_json, access_json, geom_json
            FROM read_parquet('{paths["segments"]}')
            """
        )
        columns = [d[0] for d in rows.description]
        seen = 0
        while True:
            batch = rows.fetchmany(5000)
            if not batch:
                break
            for record in batch:
                builder.add_segment(dict(zip(columns, record, strict=True)))
                seen += 1
            LOG.debug("segments processed=%d nodes=%d edges=%d",
                      seen, len(builder.node_lon), len(builder.edges))
        LOG.info("segments=%d -> nodes=%d edges=%d", seen, len(builder.node_lon), len(builder.edges))
        for key, value in sorted(builder.stats.items()):
            LOG.info("  %-28s %d", key, value)

        sampler = TerrainSampler()
        sampler.preload(region.bbox)

        summary = {
            "region": region.key,
            "label": region.label,
            "graph": pack_graph(builder, sampler, region, out_dir / f"{region.key}.graph.spw"),
            "addresses": pack_addresses(con, paths["addresses"], region, out_dir / f"{region.key}.addr.spw"),
            "places": pack_places(con, paths["places"], region, out_dir / f"{region.key}.places.spw"),
            "green": pack_green(con, paths["green"], region, out_dir / f"{region.key}.green.spw"),
        }
    finally:
        con.close()

    summary["elapsed_s"] = round(time.monotonic() - started, 1)
    LOG.info("=== region %s done in %.1fs ===", region.key, summary["elapsed_s"])
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m data.pipeline", description=__doc__)
    parser.add_argument("command", choices=("build", "extract", "regions"))
    parser.add_argument("--region", action="append", choices=sorted(REGIONS),
                        help="region key; repeatable. Default: all regions.")
    parser.add_argument("--force", action="store_true", help="refetch cached Overture extracts")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--out-dir", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    keys = args.region or list(REGIONS)

    if args.command == "regions":
        for key in keys:
            r = REGIONS[key]
            print(f"{r.key:6s} {r.label:28s} bbox={r.bbox} center={r.center}")
        return 0

    if args.command == "extract":
        for key in keys:
            extract_all(REGIONS[key], cache_dir=args.cache_dir, force=args.force)
        return 0

    summaries = [
        build_region(REGIONS[key], args.cache_dir, args.out_dir, args.force) for key in keys
    ]

    # A manifest lets the API enumerate regions without opening every container.
    manifest = {
        "regions": [
            {
                "key": s["region"],
                "label": s["label"],
                "center": list(REGIONS[s["region"]].center),
                "bbox": list(REGIONS[s["region"]].bbox),
                "n_nodes": s["graph"]["n_nodes"],
                "n_edges": s["graph"]["n_edges"],
                "n_addresses": s["addresses"]["count"],
                "n_places": s["places"]["count"],
            }
            for s in summaries
        ],
        "default_region": DEFAULT_REGION,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    total = sum(
        s[k]["bytes"] for s in summaries for k in ("graph", "addresses", "places", "green")
    )
    LOG.info("build complete: %d region(s), %.1f MB of artifacts", len(summaries), total / 1e6)
    for s in summaries:
        LOG.info(
            "  %-5s nodes=%-7d edges=%-7d addr=%-7d places=%-6d",
            s["region"], s["graph"]["n_nodes"], s["graph"]["n_edges"],
            s["addresses"]["count"], s["places"]["count"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
