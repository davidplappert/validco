"""Loading and caching the baked datasets.

The registry is the only thing that touches the filesystem, and it caches at
module scope. That single fact is what makes cold starts acceptable: the first
request in a Lambda container pays to decode the arrays it touches, and every
later request on that container gets them free.

Each dataset within a region is loaded lazily too, so a geocode request never
materialises the routing graph and a plan request never materialises green space
unless the walker asked to prefer it.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path

from ..container import Container
from ..models.location import Coordinate
from ..models.region import Region
from .addresses import AddressIndex
from .catalog import READY, RegionCatalog
from .graph import WalkGraph
from .green import GreenIndex
from .places import PlaceIndex

LOG = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

#: Lambda gives every container a writable /tmp that survives between
#: invocations, so a downloaded region is fetched once per container rather
#: than once per request.
CACHE_DIR = Path(tempfile.gettempdir()) / "stepwise-regions"


class RegionDatasets:
    """The four datasets for one region, each loaded on first use.

    A region is either **bundled** — shipped inside the deployment package, as
    the two showcase cities are — or **on demand**, built into S3 when someone
    first asks for it. Bundled regions cost nothing to reach; on-demand ones are
    downloaded to the container's /tmp once and reused for its lifetime.
    """

    def __init__(self, key: str, data_dir: Path, catalog: RegionCatalog | None = None):
        """Record which region and where its files live; load nothing yet."""
        self.key = key
        self.data_dir = data_dir
        self.catalog = catalog
        self._graph: WalkGraph | None = None
        self._addresses: AddressIndex | None = None
        self._places: PlaceIndex | None = None
        self._green: GreenIndex | None = None

    def _load(self, suffix: str) -> Container:
        """Open one of this region's containers, fetching it if it is remote."""
        bundled = self.data_dir / f"{self.key}.{suffix}.spw"
        if bundled.exists():
            return Container.load(bundled)

        if self.catalog is None or not self.catalog.enabled:
            raise FileNotFoundError(f"no dataset {suffix!r} for region {self.key!r}")

        cached = CACHE_DIR / f"{self.key}.{suffix}.spw"
        if not cached.exists():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            # Download to a temporary name and rename, so a container that dies
            # mid-download cannot leave a truncated file that later loads as a
            # corrupt graph.
            staging = cached.with_suffix(".partial")
            if not self.catalog.download(self.key, suffix, str(staging)):
                raise FileNotFoundError(
                    f"region {self.key!r} has no {suffix!r} dataset in the catalog"
                )
            staging.replace(cached)
            LOG.info(
                "cached region artifact key=%s suffix=%s bytes=%d",
                self.key,
                suffix,
                cached.stat().st_size,
            )
        return Container.load(cached)

    @property
    def graph(self) -> WalkGraph:
        """The routable network, loaded on first access."""
        if self._graph is None:
            self._graph = WalkGraph(self._load("graph"))
        return self._graph

    @property
    def addresses(self) -> AddressIndex:
        """The geocoding corpus, loaded on first access."""
        if self._addresses is None:
            self._addresses = AddressIndex(self._load("addr"))
        return self._addresses

    @property
    def places(self) -> PlaceIndex:
        """Candidate destinations, loaded on first access."""
        if self._places is None:
            self._places = PlaceIndex(self._load("places"))
        return self._places

    @property
    def green(self) -> GreenIndex:
        """Green-space proximity, loaded on first access."""
        if self._green is None:
            self._green = GreenIndex(self._load("green"))
        return self._green

    def is_local(self, suffix: str) -> bool:
        """Whether one container can be opened without touching the network.

        True for a bundled region always, and for an on-demand region once this
        Lambda container has downloaded it to /tmp.

        This exists for autocomplete. Suggestions run on every keystroke and
        iterate whatever regions are known, so without this check a single
        keypress could trigger an S3 download of every on-demand region's
        address container — several megabytes each, repeated per key, on a code
        path that is supposed to cost nothing.
        """
        if (self.data_dir / f"{self.key}.{suffix}.spw").exists():
            return True
        return (CACHE_DIR / f"{self.key}.{suffix}.spw").exists()

    def loaded(self) -> dict[str, bool]:
        """Which datasets this container has actually materialised.

        Exposed through the health endpoint, where it makes cold-start behaviour
        observable instead of a matter of inference from latency.
        """
        return {
            "graph": self._graph is not None,
            "addresses": self._addresses is not None,
            "places": self._places is not None,
            "green": self._green is not None,
        }


class DatasetRegistry:
    """Every region's datasets, plus the manifest describing them."""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR, catalog: RegionCatalog | None = None):
        """Point the registry at a data directory without reading it yet."""
        self.data_dir = data_dir
        self.catalog = catalog if catalog is not None else RegionCatalog()
        self._manifest: dict | None = None
        self._regions: dict[str, Region] | None = None
        self._datasets: dict[str, RegionDatasets] = {}
        self._catalog_regions: dict[str, Region] = {}
        self._catalog_loaded_at = 0.0

    @property
    def manifest(self) -> dict:
        """The build manifest, read once."""
        if self._manifest is None:
            self._manifest = json.loads((self.data_dir / "manifest.json").read_text())
            LOG.info("manifest loaded regions=%s", [r["key"] for r in self._manifest["regions"]])
        return self._manifest

    @property
    def bundled_regions(self) -> dict[str, Region]:
        """Regions shipped inside the deployment package."""
        if self._regions is None:
            self._regions = {r["key"]: Region(**r) for r in self.manifest["regions"]}
        return self._regions

    @property
    def regions(self) -> dict[str, Region]:
        """Every region available to serve plans — bundled plus ready on-demand.

        The catalogue listing is cached for a short window: it costs an S3 LIST
        plus a GET per region, and coverage does not change between one request
        and the next.
        """
        merged = dict(self.bundled_regions)
        merged.update(self._ready_catalog_regions())
        return merged

    def _ready_catalog_regions(self, ttl_s: float = 30.0) -> dict[str, Region]:
        """On-demand regions that have finished building."""
        if not self.catalog.enabled:
            return {}
        now = time.time()
        if self._catalog_regions and (now - self._catalog_loaded_at) < ttl_s:
            return self._catalog_regions

        found: dict[str, Region] = {}
        for status in self.catalog.list():
            if status.state != READY or not status.bbox:
                continue
            stats = status.stats or {}
            found[status.key] = Region(
                key=status.key,
                label=status.label,
                center=status.center
                or [
                    (status.bbox[1] + status.bbox[3]) / 2,
                    (status.bbox[0] + status.bbox[2]) / 2,
                ],
                bbox=status.bbox,
                n_nodes=stats.get("n_nodes", 0),
                n_edges=stats.get("n_edges", 0),
                n_addresses=stats.get("n_addresses", 0),
                n_places=stats.get("n_places", 0),
            )
        self._catalog_regions = found
        self._catalog_loaded_at = now
        LOG.debug("catalog regions refreshed count=%d", len(found))
        return found

    def invalidate(self) -> None:
        """Drop the cached catalogue listing.

        Called after a build completes, so the region it produced is visible to
        the very next request rather than up to a TTL later.
        """
        self._catalog_regions = {}
        self._catalog_loaded_at = 0.0

    @property
    def default_region_key(self) -> str:
        """The region the frontend opens on when nothing else is known."""
        return self.manifest["default_region"]

    def keys(self) -> list[str]:
        """Every known region key."""
        return list(self.regions)

    def region(self, key: str) -> Region:
        """One region model, or ``KeyError`` naming the valid keys."""
        try:
            return self.regions[key]
        except KeyError:
            raise KeyError(f"unknown region {key!r}; known: {sorted(self.regions)}") from None

    def datasets(self, key: str) -> RegionDatasets:
        """The datasets for one region, cached across requests."""
        self.region(key)  # validates, and raises with a useful message
        cached = self._datasets.get(key)
        if cached is None:
            cached = RegionDatasets(key, self.data_dir, self.catalog)
            self._datasets[key] = cached
            LOG.info("region datasets registered key=%s", key)
        return cached

    def region_for(self, coordinate: Coordinate) -> str | None:
        """Which region's bounding box contains a point, if any."""
        for key, region in self.regions.items():
            if region.contains(coordinate):
                return key
        return None

    def status(self) -> dict[str, dict[str, bool]]:
        """What each registered region has loaded so far, for the health check."""
        return {key: datasets.loaded() for key, datasets in self._datasets.items()}


#: Process-wide registry. Sharing one instance across invocations is the entire
#: cold-start strategy — importing this module in the handler is what warms it.
REGISTRY = DatasetRegistry()
