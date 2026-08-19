"""Round-trip tests for the binary container.

The writer runs offline and the reader runs in Lambda, so a format mismatch
would only surface as a production cold-start failure. These tests exercise both
halves against each other.
"""

from __future__ import annotations

from array import array

import pytest
from stepwise.container import Container, ContainerWriter


def test_round_trip_preserves_every_dtype(tmp_path):
    writer = ContainerWriter()
    writer.add("floats", "f", [1.5, -2.25, 0.0])
    writer.add("u32", "I", [0, 7, 4_294_967_295])
    writer.add("i16", "h", [-32768, 0, 32767])
    writer.add("u8", "B", [0, 128, 255])
    writer.meta = {"region": "test", "nested": {"a": [1, 2, 3]}}
    path = tmp_path / "t.spw"
    writer.write(path)

    c = Container.load(path)
    assert list(c.get("floats")) == [1.5, -2.25, 0.0]
    assert list(c.get("u32")) == [0, 7, 4_294_967_295]
    assert list(c.get("i16")) == [-32768, 0, 32767]
    assert list(c.get("u8")) == [0, 128, 255]
    assert c.meta["region"] == "test"
    assert c.meta["nested"]["a"] == [1, 2, 3]


def test_arrays_are_decoded_lazily_and_cached(tmp_path):
    """A request that only geocodes must not pay to decode routing geometry."""
    writer = ContainerWriter()
    writer.add("a", "I", range(100))
    writer.add("b", "I", range(100))
    path = tmp_path / "t.spw"
    writer.write(path)

    c = Container.load(path)
    assert c._cache == {}
    first = c.get("a")
    assert set(c._cache) == {"a"}
    assert c.get("a") is first  # same object, not re-decoded


def test_empty_array_round_trips(tmp_path):
    writer = ContainerWriter()
    writer.add("nothing", "f", [])
    path = tmp_path / "t.spw"
    writer.write(path)
    assert len(Container.load(path).get("nothing")) == 0


def test_accepts_a_prebuilt_array(tmp_path):
    writer = ContainerWriter()
    writer.add("vals", "f", array("f", [1.0, 2.0]))
    path = tmp_path / "t.spw"
    writer.write(path)
    assert list(Container.load(path).get("vals")) == [1.0, 2.0]


def test_rejects_bad_input(tmp_path):
    writer = ContainerWriter()
    with pytest.raises(ValueError, match="unsupported dtype"):
        writer.add("x", "q", [1])
    writer.add("x", "I", [1])
    with pytest.raises(ValueError, match="duplicate"):
        writer.add("x", "I", [2])


def test_unknown_array_names_itself_and_its_siblings(tmp_path):
    writer = ContainerWriter()
    writer.add("present", "I", [1])
    path = tmp_path / "t.spw"
    writer.write(path)
    with pytest.raises(KeyError, match="present"):
        Container.load(path).get("absent")


def test_rejects_a_non_container_file(tmp_path):
    path = tmp_path / "bad.spw"
    path.write_bytes(b"not a stepwise file at all, really")
    with pytest.raises(ValueError, match="not a StepWise container"):
        Container.load(path)
