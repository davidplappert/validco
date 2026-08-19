"""A tiny columnar container for the baked Overture datasets.

Why this exists
---------------
The Lambda needs the San Francisco walking graph in memory on every cold start.
Anything JSON-shaped costs hundreds of milliseconds to parse and several times
the memory of the underlying numbers, and pulling in numpy would mean an
architecture-specific build for a handful of array reads. So the datasets ship
as flat little-endian arrays behind a JSON index, and the reader is
``array.array.frombytes`` — a C-speed memcpy from the standard library.

Layout::

    magic        8 bytes   b"STEPWISE"
    version      uint32    container format version (not dataset version)
    header_len   uint32    byte length of the JSON header
    header       JSON      {"arrays": {...}, "meta": {...}}
    blocks       raw       every array back to back, in header order

Each entry in ``arrays`` is ``{"dtype": <typecode>, "offset": <int>, "count":
<int>}`` where ``offset`` is relative to the start of the block region. Type
codes are the ones :mod:`array` uses, restricted to the fixed-width set below so
the format does not depend on the host's C type sizes.

Both the offline builder and the runtime reader use this module, so the write
and read paths can never drift apart.
"""

from __future__ import annotations

import json
import logging
import struct
import sys
from array import array
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

MAGIC = b"STEPWISE"
CONTAINER_VERSION = 1
_HEAD = struct.Struct("<8sII")

# Typecode -> itemsize. `array` guarantees these widths for the codes we use on
# every platform CPython supports, but we assert rather than trust.
DTYPES: dict[str, int] = {
    "b": 1,  # int8
    "B": 1,  # uint8
    "h": 2,  # int16
    "H": 2,  # uint16
    "i": 4,  # int32
    "I": 4,  # uint32
    "f": 4,  # float32
    "d": 8,  # float64
}


def _check_widths() -> None:
    for code, width in DTYPES.items():
        actual = array(code).itemsize
        if actual != width:
            raise RuntimeError(f"array typecode {code!r} is {actual}B here, expected {width}B")


_check_widths()


class Container:
    """An open, memory-resident container.

    Arrays are decoded lazily and cached: a cold start that only geocodes an
    address never pays to materialise the routing graph's geometry columns.
    """

    __slots__ = ("_blob", "_index", "_cache", "meta", "path")

    def __init__(self, blob: bytes, index: dict[str, Any], meta: dict[str, Any], path: str = ""):
        self._blob = blob
        self._index = index
        self._cache: dict[str, array] = {}
        self.meta = meta
        self.path = path

    @classmethod
    def load(cls, path: str | Path) -> Container:
        path = Path(path)
        raw = path.read_bytes()
        magic, version, header_len = _HEAD.unpack_from(raw, 0)
        if magic != MAGIC:
            raise ValueError(f"{path}: not a StepWise container (magic={magic!r})")
        if version != CONTAINER_VERSION:
            raise ValueError(f"{path}: container version {version}, expected {CONTAINER_VERSION}")
        head_end = _HEAD.size + header_len
        header = json.loads(raw[_HEAD.size : head_end].decode("utf-8"))
        LOG.debug(
            "loaded container path=%s bytes=%d arrays=%d meta_keys=%s",
            path,
            len(raw),
            len(header["arrays"]),
            sorted(header.get("meta", {})),
        )
        return cls(raw[head_end:], header["arrays"], header.get("meta", {}), str(path))

    def __contains__(self, name: str) -> bool:
        return name in self._index

    def names(self) -> list[str]:
        return list(self._index)

    def get(self, name: str) -> array:
        """Decode (and cache) one column."""
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        try:
            spec = self._index[name]
        except KeyError:
            raise KeyError(f"{self.path}: no array {name!r}; have {sorted(self._index)}") from None
        dtype, offset, count = spec["dtype"], spec["offset"], spec["count"]
        width = DTYPES[dtype]
        buf = array(dtype)
        buf.frombytes(self._blob[offset : offset + count * width])
        if sys.byteorder != "little":
            buf.byteswap()
        if len(buf) != count:
            raise ValueError(f"{self.path}: array {name!r} truncated ({len(buf)} of {count})")
        LOG.debug("decoded array name=%s dtype=%s count=%d", name, dtype, count)
        self._cache[name] = buf
        return buf


class ContainerWriter:
    """Accumulates named arrays, then writes them out in one pass."""

    def __init__(self) -> None:
        self._arrays: list[tuple[str, array]] = []
        self.meta: dict[str, Any] = {}

    def add(self, name: str, dtype: str, values) -> None:
        if dtype not in DTYPES:
            raise ValueError(f"unsupported dtype {dtype!r}; known: {sorted(DTYPES)}")
        if any(name == existing for existing, _ in self._arrays):
            raise ValueError(f"duplicate array name {name!r}")
        buf = values if isinstance(values, array) and values.typecode == dtype else array(dtype, values)
        self._arrays.append((name, buf))
        LOG.debug("staged array name=%s dtype=%s count=%d", name, dtype, len(buf))

    def write(self, path: str | Path) -> int:
        index: dict[str, Any] = {}
        offset = 0
        blocks: list[bytes] = []
        for name, buf in self._arrays:
            if sys.byteorder != "little":
                buf = array(buf.typecode, buf)
                buf.byteswap()
            raw = buf.tobytes()
            index[name] = {"dtype": buf.typecode, "offset": offset, "count": len(buf)}
            blocks.append(raw)
            offset += len(raw)

        header = json.dumps({"arrays": index, "meta": self.meta}, separators=(",", ":")).encode()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            fh.write(_HEAD.pack(MAGIC, CONTAINER_VERSION, len(header)))
            fh.write(header)
            for raw in blocks:
                fh.write(raw)
        total = _HEAD.size + len(header) + offset
        LOG.info("wrote container path=%s arrays=%d bytes=%d", path, len(index), total)
        return total
