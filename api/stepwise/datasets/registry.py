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
from pathlib import Path

from ..container import Container
from ..models.location import Coordinate
from ..models.region import Region
from .addresses import AddressIndex
from .graph import WalkGraph
from .green import GreenIndex
from .places import PlaceIndex

LOG = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class RegionDatasets:
    """The four datasets for one region, each loaded on first use."""

    def __init__(self, key: str, data_dir: Path):
        """Record which region and where its files live; load nothing yet."""
        self.key = key
        self.data_dir = data_dir
        self._graph: WalkGraph | None = None
        self._addresses: AddressIndex | None = None
        self._places: PlaceIndex | None = None
        self._green: GreenIndex | None = None

    def _load(self, suffix: str) -> Container:
        """Open one of this region's containers by filename suffix."""
        return Container.load(self.data_dir / f"{self.key}.{suffix}.spw")

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

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR):
        """Point the registry at a data directory without reading it yet."""
        self.data_dir = data_dir
        self._manifest: dict | None = None
        self._regions: dict[str, Region] | None = None
        self._datasets: dict[str, RegionDatasets] = {}

    @property
    def manifest(self) -> dict:
        """The build manifest, read once."""
        if self._manifest is None:
            self._manifest = json.loads((self.data_dir / "manifest.json").read_text())
            LOG.info("manifest loaded regions=%s", [r["key"] for r in self._manifest["regions"]])
        return self._manifest

    @property
    def regions(self) -> dict[str, Region]:
        """Region models keyed by their short key."""
        if self._regions is None:
            self._regions = {r["key"]: Region(**r) for r in self.manifest["regions"]}
        return self._regions

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
            cached = RegionDatasets(key, self.data_dir)
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
