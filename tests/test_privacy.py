"""Guards against personal data reaching this public repository.

This repository is public. During development it was seeded with a real home
address and a real body weight, which is exactly the kind of thing that gets
committed once and then lives in the git history forever. These tests fail the
build if either reappears.

The patterns below are deliberately specific — a street name, a coordinate, a
weight — rather than a general "looks like an address" heuristic, because a
vague test that fires on the fixture data would be turned off within a week.

What counts as personal here is drawn narrowly on purpose. A particular
dwelling identifies the people inside it: the residential street name and the
coordinates that land on its roof are the leak. A town of several thousand and
the ZIP code covering all of them are not personal data, and neither is a civic
building whose address the city publishes itself — the form ships with one as
its default start point. Forbidding the town and the postcode outright banned
public record along with the private detail, and would have made a usable
default impossible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Directories that are generated, vendored, or binary.
#:
#: "data" used to be on this list, to skip the baked ``.spw`` containers — and
#: because the match is on *any* path component, it silently excluded the whole
#: of ``data/pipeline/``, which is source. A street name sat in a docstring
#: there, in a public repository, passing this suite. The containers are
#: already excluded by ``SUFFIXES`` being an allowlist, so the entry bought
#: nothing and cost the coverage that mattered.
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "cache",
    ".next",
    "out",
    "cdk.out",
    "__pycache__",
    "test-results",
    "playwright-report",
    ".ruff_cache",
    ".pytest_cache",
}

#: Text files worth scanning.
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".yml", ".json", ".sh", ".css"}

#: Specific personal identifiers that must never appear. Each entry is
#: (pattern, what it is), so a failure says what leaked rather than just
#: "pattern matched".
#:
#: Every entry has to point at one household. The town name and its postcode
#: were listed here once and are not any more: they are shared by everyone who
#: lives there and by the public buildings among them, so banning them said
#: nothing about the residence while forbidding the civic address the form uses
#: as its default.
FORBIDDEN: list[tuple[str, str]] = [
    # Bare, not "main (dr|drive)". The narrower form missed "100 N Main"
    # in a test docstring: the street name and the house number, with the
    # suffix left off. A street name identifies the residence with or without
    # the word "Drive" after it.
    (r"\bmain\b", "a private residential street name"),
    (r"40\.917\d*", "the latitude of a private residence"),
    (r"-89\.502\d*", "the longitude of a private residence"),
    (r"\b361\s*(lb|lbs|pounds)\b", "a real person's body weight"),
    (r"weight_lb[\"']?\s*[:=]\s*361\b", "a real person's body weight"),
]


def source_files() -> list[Path]:
    """Every text file in the repository worth scanning."""
    found = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() not in SUFFIXES:
            continue
        found.append(path)
    return found


@pytest.mark.parametrize("pattern,description", FORBIDDEN)
def test_no_personal_data_in_the_repository(pattern: str, description: str):
    """Fail the build if a personal identifier appears anywhere in the source."""
    compiled = re.compile(pattern, re.IGNORECASE)
    offenders: list[str] = []

    for path in source_files():
        # This file necessarily contains the patterns it forbids.
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")

    assert not offenders, f"found {description} in a public repository:\n  " + "\n  ".join(
        offenders[:20]
    )


def test_the_scanner_actually_scans_something():
    """A guard whose file list is empty would pass while checking nothing."""
    files = source_files()
    assert len(files) > 50, f"expected to scan the repository, found {len(files)} files"
    names = {f.name for f in files}
    assert "handler.py" in names and "page.tsx" in names


#: The start address the form is pre-filled with. Chillicothe City Hall: the
#: seat of the town's government, listed on the city's own website, so it
#: identifies an office rather than a person. The street is spelled out because
#: Overture stores it as "North Second Street" and the geocoder does not fold
#: "2nd" into "Second".
DEFAULT_START_ADDRESS = "908 N Second St, Chillicothe, IL 61523"


def test_fixture_addresses_are_public_places():
    """The addresses used in tests should be civic or commercial, not homes.

    Not enforceable automatically — a street address does not announce whether
    anyone lives at it — so this documents the intent and pins the specific
    values, making a swap to a residential address a visible change in review
    rather than an invisible one.
    """
    expected = {
        "100 N Main St, Morton, IL",  # a commercial main street
        "1100 California St, San Francisco",  # Grace Cathedral, Nob Hill
        "1 Dr Carlton B Goodlett Pl",  # San Francisco City Hall
    }
    conftest = (ROOT / "tests" / "conftest.py").read_text()
    api_tests = (ROOT / "tests" / "test_api.py").read_text()
    combined = conftest + api_tests
    assert any(address.split(",")[0] in combined for address in expected)


def test_the_default_start_address_is_a_public_building():
    """The address the form ships with must be civic, not residential.

    It matters more than a test fixture does: it is what a first-time visitor
    plans a walk from, what every screenshot of the app shows, and the one
    address in the repository that nobody has to type. A house would be a
    private address published to every user of the site.
    """
    form = (ROOT / "web" / "src" / "components" / "form" / "PlanForm.tsx").read_text()
    assert DEFAULT_START_ADDRESS in form, (
        f"the form's default start address should be {DEFAULT_START_ADDRESS} "
        "(Chillicothe City Hall), a building on public record"
    )


def test_the_scanner_reaches_the_build_pipeline():
    """The pipeline is source, and must be scanned like the rest of it.

    ``SKIP_DIRS`` matches on *any* path component, so listing "data" to skip
    the baked ``.spw`` containers also excluded the whole of ``data/pipeline``.
    A street name sat in a docstring there, in a public repository, while this
    suite reported success. The containers never needed the entry — ``SUFFIXES``
    is an allowlist and does not include ``.spw`` — so it cost coverage and
    bought nothing.
    """
    scanned = {str(p.relative_to(ROOT)) for p in source_files()}
    assert "data/pipeline/build.py" in scanned
    assert "data/pipeline/config.py" in scanned


def test_no_region_centre_is_a_residence():
    """A region's centre is where the map opens; it must name a place.

    ``pia`` was centred on a house. Nothing in the app said so — it simply
    opened on somebody's roof whenever there was no result to show, which is a
    more public disclosure than a string in a source file.
    """
    manifest = json.loads((ROOT / "api" / "stepwise" / "data" / "manifest.json").read_text())
    for region in manifest["regions"]:
        latitude, longitude = region["center"]
        assert not (40.91 < latitude < 40.92 and -89.51 < longitude < -89.50), (
            f"region {region['key']!r} is centred on a private residence"
        )
