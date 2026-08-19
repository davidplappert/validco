"""Tests for the on-demand coverage catalogue and the builder's guard rails.

The first on-demand build ever attempted failed here, and failed quietly: the
builder function was deployed without ``REGION_BUCKET``, which left its
catalogue disabled. Every progress write became a no-op, so the region sat at
"queued" while the extraction actually ran to completion, and the only symptom
was a botocore complaint about an empty bucket name ninety seconds later — in a
log group nobody was watching. The status document stayed at "building" until it
went stale twenty minutes on.

The tests below pin both halves of the fix: writes that must refuse to no-op,
and a builder that says so before spending the ninety seconds.
"""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import pytest
from stepwise.datasets.catalog import (
    BUILDING,
    MAX_AREA_SQ_DEG,
    MIN_SPAN_DEG,
    READY,
    RegionCatalog,
    RegionKey,
    RegionStatus,
    clamp_bbox,
)

ROOT = Path(__file__).resolve().parents[1]


class TestRegionKey:
    def test_qualifies_by_country_and_state(self):
        """There are Austins in Texas, Minnesota and Michigan."""
        texas = RegionKey.from_name("Austin", "US", "US-TX")
        minnesota = RegionKey.from_name("Austin", "US", "US-MN")
        assert texas != minnesota

    def test_folds_accents_so_one_city_is_one_key(self):
        assert RegionKey.from_name("Zürich") == RegionKey.from_name("Zurich")

    def test_slugifies_punctuation_and_spaces(self):
        assert RegionKey.from_name("Galesburg, IL") == "galesburg-il"

    def test_is_bounded_in_length(self):
        assert len(RegionKey.from_name("x" * 500)) <= 64

    def test_unrepresentable_input_still_yields_a_key(self):
        """A key is used as an S3 path; it can never be empty."""
        assert RegionKey.from_name("!!!") == "unknown"


class TestRegionStatus:
    def test_a_fresh_build_is_not_stale(self):
        status = RegionStatus(key="x", state=BUILDING, updated_at=time.time())
        assert status.is_stale is False

    def test_an_abandoned_build_goes_stale(self):
        """Without this, one killed Lambda poisons a city permanently."""
        status = RegionStatus(key="x", state=BUILDING, updated_at=time.time() - 10_000)
        assert status.is_stale is True

    def test_a_finished_build_never_goes_stale(self):
        status = RegionStatus(key="x", state=READY, updated_at=0.0)
        assert status.is_stale is False
        assert status.is_ready is True

    def test_round_trips_through_its_dictionary(self):
        status = RegionStatus(key="x", label="X", bbox=[0.0, 0.0, 1.0, 1.0], progress=0.5)
        assert RegionStatus(**status.to_dict()).to_dict() == status.to_dict()

    def test_an_error_is_only_reported_when_there_is_one(self):
        assert "error" not in RegionStatus(key="x").to_dict()
        assert "error" in RegionStatus(key="x", error="boom").to_dict()


class TestClampBbox:
    def test_a_village_is_grown_to_something_walkable(self):
        west, south, east, north = clamp_bbox([0.0, 0.0, 0.001, 0.001])
        assert (east - west) == pytest.approx(MIN_SPAN_DEG)
        assert (north - south) == pytest.approx(MIN_SPAN_DEG)

    def test_a_country_is_shrunk_to_something_buildable(self):
        west, south, east, north = clamp_bbox([-10.0, 40.0, 10.0, 55.0])
        assert (east - west) * (north - south) == pytest.approx(MAX_AREA_SQ_DEG)

    def test_the_centre_is_preserved(self):
        """A clamped window must still be centred on what was asked for."""
        west, south, east, north = clamp_bbox([-10.0, 40.0, 10.0, 55.0])
        assert (west + east) / 2 == pytest.approx(0.0)
        assert (south + north) / 2 == pytest.approx(47.5)

    def test_a_city_sized_window_is_left_alone(self):
        original = [-90.44, 40.90, -90.30, 40.99]
        assert clamp_bbox(original) == pytest.approx(original)


class TestCatalogWithoutABucket:
    """A catalogue with no bucket is the local and test configuration.

    Reads degrading to "nothing here" is correct — the bundled regions are the
    whole world. Writes degrading to silence is not, and that distinction is the
    bug this class exists to pin.
    """

    @pytest.fixture
    def catalog(self) -> RegionCatalog:
        """A catalogue with no bucket configured."""
        return RegionCatalog(bucket="")

    def test_is_not_enabled(self, catalog):
        assert catalog.enabled is False

    def test_reads_report_nothing_rather_than_failing(self, catalog):
        assert catalog.get("galesburg-il") is None
        assert catalog.list() == []
        assert catalog.download("galesburg-il", "graph", "/tmp/unused") is False

    def test_uploading_refuses_to_be_silent(self, catalog):
        """The failure that shipped: the upload built an empty bucket name."""
        with pytest.raises(RuntimeError, match="REGION_BUCKET"):
            catalog.upload("galesburg-il", "graph", "/tmp/unused")

    def test_status_writes_do_not_raise(self, catalog):
        """Progress reporting is best-effort; only publishing is load-bearing."""
        status = RegionStatus(key="galesburg-il")
        catalog.progress(status, 0.5, "segments")
        assert status.progress == 0.5

    def test_a_bucket_from_the_environment_enables_it(self, monkeypatch):
        monkeypatch.setenv("REGION_BUCKET", "some-bucket")
        assert RegionCatalog().enabled is True

    def test_artifact_keys_are_namespaced_per_region(self, catalog):
        assert catalog.artifact_key("galesburg-il", "graph") == (
            "regions/galesburg-il/galesburg-il.graph.spw"
        )


@pytest.fixture(scope="module")
def builder_handler():
    """The builder's entry module, imported the way its bundle lays it out."""
    pytest.importorskip("duckdb", reason="the builder's pipeline needs DuckDB")
    # `data.pipeline` is imported from the repository root, which is a source
    # root here rather than an installed package.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("handler")


class TestBuilderStartupGuard:
    """The builder must refuse to start rather than fail after the extraction."""

    def test_says_so_immediately_when_no_bucket_is_configured(self, builder_handler, monkeypatch):
        """Ninety seconds of extraction with nowhere to publish helps nobody."""
        monkeypatch.delenv("REGION_BUCKET", raising=False)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="REGION_BUCKET"):
            builder_handler.handler({"key": "galesburg-il", "place": "Galesburg, IL"})
        # Fast enough to prove nothing was extracted first.
        assert time.monotonic() - started < 5.0

    def test_every_stage_has_a_progress_weight(self, builder_handler):
        """A missing weight is a KeyError in the middle of a long build."""
        assert set(builder_handler.STAGES) >= {
            "resolve",
            "segments",
            "addresses",
            "places",
            "green",
            "graph",
            "terrain",
            "pack",
            "upload",
        }

    def test_progress_weights_rise_and_stop_short_of_done(self, builder_handler):
        """Only `complete` may report 1.0, and the bar must never go backwards."""
        fractions = [fraction for fraction, _ in builder_handler.STAGES.values()]
        assert fractions == sorted(fractions)
        assert max(fractions) < 1.0
