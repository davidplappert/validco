"""Guards against personal data reaching this public repository.

This repository is public. During development it was seeded with a real home
address and a real body weight, which is exactly the kind of thing that gets
committed once and then lives in the git history forever. These tests fail the
build if either reappears.

The patterns below are deliberately specific — a street name, a postcode, a
coordinate — rather than a general "looks like an address" heuristic, because a
vague test that fires on the fixture data would be turned off within a week.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Directories that are generated, vendored, or binary.
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
    "data",
}

#: Text files worth scanning.
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".yml", ".json", ".sh", ".css"}

#: Specific personal identifiers that must never appear. Each entry is
#: (pattern, what it is), so a failure says what leaked rather than just
#: "pattern matched".
FORBIDDEN: list[tuple[str, str]] = [
    (r"\bmain\s+(dr|drive)\b", "a private residential street name"),
    (r"\b61523\b", "the postcode of a private residence"),
    (r"\bchillicothe\b", "the town of a private residence"),
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


def test_fixture_addresses_are_public_places():
    """The addresses used in tests should be civic or commercial, not homes.

    Not enforceable automatically — this documents the intent and pins the
    specific values, so swapping in a residential address is a visible change in
    review rather than an invisible one.
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
