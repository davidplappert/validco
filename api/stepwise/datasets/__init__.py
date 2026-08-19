"""Access to the baked Overture datasets.

Everything here is read-only and loaded from files inside the deployment
package. The write side lives in ``data/pipeline``, which runs offline.
"""

from .addresses import AddressIndex
from .graph import WalkGraph
from .green import GreenIndex
from .places import PlaceIndex
from .registry import REGISTRY, DatasetRegistry, RegionDatasets

__all__ = [
    "REGISTRY",
    "AddressIndex",
    "DatasetRegistry",
    "GreenIndex",
    "PlaceIndex",
    "RegionDatasets",
    "WalkGraph",
]
