"""Elevation sampling from AWS Open Data "Terrain Tiles".

San Francisco is the reason this project needs elevation at all: a 1.5 km walk
across Nob Hill and a 1.5 km walk through the Marina are the same distance and
wildly different exercise. Grade drives both the time estimate and the energy
cost, so it has to come from a real DEM.

The source is the ``elevation-tiles-prod`` public bucket — Terrarium-encoded
PNGs, unauthenticated, free, and derived from USGS 3DEP (~10 m) over the US.
Terrarium packs metres into RGB as::

    elevation = (R * 256 + G + B / 256) - 32768

Tiles are fetched once into a local cache and sampled bilinearly. This runs
offline at build time; the Lambda only ever sees the baked per-node numbers.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
from array import array
from pathlib import Path

import requests

from .config import TERRAIN_TILE_URL, TERRAIN_ZOOM

LOG = logging.getLogger(__name__)

TILE_SIZE = 256
CACHE_DIR = Path(__file__).resolve().parents[1] / "cache" / "terrain"

# Terrarium's sentinel for "no data" decodes to this; the ocean around SF is
# genuinely 0 m, so we only treat deep negatives as missing.
NODATA_BELOW_M = -400.0


def deg2tile(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Web Mercator tile coordinates as floats (integer part = tile, fraction = pixel)."""
    n = 2.0**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


class TerrainSampler:
    """Bilinear elevation lookup over a cached mosaic of Terrarium tiles."""

    def __init__(self, zoom: int = TERRAIN_ZOOM, cache_dir: Path = CACHE_DIR, workers: int = 8):
        self.zoom = zoom
        self.cache_dir = cache_dir
        self.workers = workers
        self._tiles: dict[tuple[int, int], array] = {}
        self._misses = 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # --- tile plumbing ----------------------------------------------------

    def _tile_path(self, x: int, y: int) -> Path:
        return self.cache_dir / f"{self.zoom}_{x}_{y}.png"

    def _fetch_tile(self, x: int, y: int) -> bytes | None:
        path = self._tile_path(x, y)
        if path.exists():
            return path.read_bytes()
        url = TERRAIN_TILE_URL.format(z=self.zoom, x=x, y=y)
        try:
            resp = requests.get(url, timeout=30)
        except requests.RequestException as exc:
            LOG.warning("terrain tile fetch failed z=%d x=%d y=%d err=%s", self.zoom, x, y, exc)
            return None
        if resp.status_code != 200:
            LOG.warning("terrain tile http=%d z=%d x=%d y=%d", resp.status_code, self.zoom, x, y)
            return None
        path.write_bytes(resp.content)
        LOG.debug(
            "fetched terrain tile z=%d x=%d y=%d bytes=%d", self.zoom, x, y, len(resp.content)
        )
        return resp.content

    @staticmethod
    def _decode(png_bytes: bytes) -> array:
        """Terrarium PNG -> row-major float32 elevations in metres."""
        import io

        from PIL import Image  # imported lazily: build-time only dependency

        with Image.open(io.BytesIO(png_bytes)) as img:
            rgb = img.convert("RGB")
            if rgb.size != (TILE_SIZE, TILE_SIZE):
                rgb = rgb.resize((TILE_SIZE, TILE_SIZE))
            raw = rgb.tobytes()

        out = array("f", bytes(4 * TILE_SIZE * TILE_SIZE))
        for i in range(TILE_SIZE * TILE_SIZE):
            r = raw[3 * i]
            g = raw[3 * i + 1]
            b = raw[3 * i + 2]
            out[i] = (r * 256.0 + g + b / 256.0) - 32768.0
        return out

    def preload(self, bbox: tuple[float, float, float, float]) -> int:
        """Download and decode every tile covering ``bbox`` (west, south, east, north)."""
        west, south, east, north = bbox
        x0, y0 = deg2tile(north, west, self.zoom)
        x1, y1 = deg2tile(south, east, self.zoom)
        xs = range(int(math.floor(x0)), int(math.floor(x1)) + 1)
        ys = range(int(math.floor(y0)), int(math.floor(y1)) + 1)
        wanted = [(x, y) for x in xs for y in ys]
        LOG.info(
            "preloading terrain zoom=%d tiles=%d x=[%d..%d] y=[%d..%d]",
            self.zoom,
            len(wanted),
            xs.start,
            xs.stop - 1,
            ys.start,
            ys.stop - 1,
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            blobs = dict(
                zip(wanted, pool.map(lambda xy: self._fetch_tile(*xy), wanted), strict=True)
            )

        loaded = 0
        for key, blob in blobs.items():
            if blob is None:
                continue
            try:
                self._tiles[key] = self._decode(blob)
                loaded += 1
            except Exception as exc:  # a corrupt cached tile should not kill the build
                LOG.warning("terrain tile decode failed key=%s err=%s", key, exc)
                self._tile_path(*key).unlink(missing_ok=True)
        LOG.info("terrain ready tiles_loaded=%d of %d", loaded, len(wanted))
        return loaded

    # --- sampling ---------------------------------------------------------

    def _pixel(self, tx: int, ty: int, px: int, py: int) -> float | None:
        """Elevation at one pixel, following the pixel into a neighbouring tile if needed."""
        tx += px // TILE_SIZE
        ty += py // TILE_SIZE
        px %= TILE_SIZE
        py %= TILE_SIZE
        tile = self._tiles.get((tx, ty))
        if tile is None:
            return None
        value = tile[py * TILE_SIZE + px]
        return None if value < NODATA_BELOW_M else value

    def sample(self, lat: float, lon: float) -> float:
        """Bilinearly interpolated elevation in metres; 0.0 where the DEM has no data.

        Bilinear rather than nearest-neighbour matters here: at ~9.5 m/px, two
        consecutive graph nodes on a hillside often land in the same pixel, and
        nearest-neighbour would flatten the block into a staircase of false
        zero-grade segments punctuated by cliffs.
        """
        fx, fy = deg2tile(lat, lon, self.zoom)
        tx, ty = int(math.floor(fx)), int(math.floor(fy))
        # Pixel-centre convention: a sample exactly at a pixel centre must
        # return that pixel's value with no blending.
        gx = (fx - tx) * TILE_SIZE - 0.5
        gy = (fy - ty) * TILE_SIZE - 0.5
        x0, y0 = math.floor(gx), math.floor(gy)
        wx, wy = gx - x0, gy - y0

        corners = [
            (self._pixel(tx, ty, int(x0), int(y0)), (1 - wx) * (1 - wy)),
            (self._pixel(tx, ty, int(x0) + 1, int(y0)), wx * (1 - wy)),
            (self._pixel(tx, ty, int(x0), int(y0) + 1), (1 - wx) * wy),
            (self._pixel(tx, ty, int(x0) + 1, int(y0) + 1), wx * wy),
        ]
        total = 0.0
        weight = 0.0
        for value, w in corners:
            if value is not None and w > 0.0:
                total += value * w
                weight += w
        if weight == 0.0:
            self._misses += 1
            return 0.0
        return total / weight

    @property
    def misses(self) -> int:
        """How many samples fell outside the loaded mosaic — should be 0."""
        return self._misses
