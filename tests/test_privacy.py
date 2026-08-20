"""Guards against personal data reaching this public repository.

This repository is public. During development it was seeded with a real home
address, the coordinates that land on it, and a real body weight — exactly the
kind of thing that gets committed once and then lives in the history forever.
These tests fail the build if any of it reappears.

Why the forbidden terms are stored as hashes
--------------------------------------------
The obvious implementation is a list of regexes containing the street name and
the coordinates. That implementation **is itself the leak it exists to
prevent**: a scanner written that way publishes, in plain text, in the most
conspicuous file on the subject, precisely the string it is protecting. The
irony is not the problem — the disclosure is.

So the terms live here only as SHA-256 digests. The scanner extracts candidate
tokens from each line, normalises and hashes them, and compares digests. It
catches the same things without naming any of them, and someone reading this
file learns that a street name is forbidden without learning which one.

The cost is real and worth stating: this can only match whole tokens and fixed
prefixes, not arbitrary regex shapes, and a failure has to describe what leaked
rather than quote it. Both are acceptable. The benefit — a privacy guard that
does not itself disclose — is not obtainable any other way.

What counts as personal here is drawn narrowly on purpose. A particular
dwelling identifies the people inside it: the residential street name and the
coordinates on its roof are the leak. A town of several thousand and the ZIP
code covering all of them are not personal data, and neither is a civic
building whose address the city publishes itself — the form ships with one as
its default start point. Forbidding the town and the postcode outright banned
public record along with the private detail, and made a usable default
impossible.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
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


def digest(value: str) -> str:
    """SHA-256 of a normalised term. The one place hashing is defined."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


#: Word tokens that must never appear, as digests. See the module docstring.
#:
#: Each entry is (digest, what it is), so a failure says what leaked rather
#: than just "pattern matched" — and without reprinting the term.
FORBIDDEN_WORDS: list[tuple[str, str]] = [
    (
        "c604dabc9591840978d2585238a908898b22e3337b207dc722ae208eb204c291",
        "a private residential street name",
    ),
]

#: Coordinate prefixes that must never appear, as digests of the value
#: truncated to three decimal places — roughly a hundred metres, which is the
#: resolution at which a coordinate stops describing an area and starts
#: describing a building.
FORBIDDEN_COORDINATES: list[tuple[str, str]] = [
    (
        "dd6a50a73f0fea9a6c55a0ec0313acb7845bf7246abda987ad67fafd3c145017",
        "the latitude of a private residence",
    ),
    (
        "b1ea2c4d1feaff2d4f09582fb2f84e15549412b25d509fe4ee5925b802c141f1",
        "the longitude of a private residence",
    ),
]

#: A number that is only personal in a weight-shaped context. Hashing it alone
#: would fire on any incidental occurrence — a byte count, a line number — so
#: the line must look like a weight before its numbers are considered at all.
FORBIDDEN_WEIGHTS: list[tuple[str, str]] = [
    (
        "73daa9289ddd08a53ba86f065ddb07bf915aba208bec652e999613d2a8444228",
        "a real person's body weight",
    ),
]

#: Runs of letters. Note these are *runs*, not words: punctuation separates
#: them, so an escape sequence butted against a name yields one run.
WORD = re.compile(r"[A-Za-z]{3,}")

#: Shortest substring worth hashing. Below this, collisions with ordinary
#: English words stop being interesting and start being noise.
MIN_FRAGMENT = 5


def fragments(line: str) -> list[str]:
    """Every substring of every letter run, down to ``MIN_FRAGMENT``.

    Matching whole runs is not enough, and the proof is that this guard
    reported success while the forbidden street name sat in ``CLAUDE.md``. It
    was written inside a regex, immediately after a ``\b`` escape, and because
    a backslash is not a letter the run the tokenizer saw was the escape's
    letter joined to the name — which hashes to something else entirely.

    That is the *same* adjacency mistake that made the history rewrite miss
    this file on its first pass, appearing one layer up in the thing meant to
    catch it. Any scheme that depends on where a name starts and stops will
    keep failing this way, so this one gives up on boundaries: every substring
    of every run is hashed, and a name is caught wherever it is embedded.

    The cost is O(n²) hashes per run. Runs are short, so in practice this adds
    well under a second across the repository — cheap for the class of miss it
    removes.
    """
    out: list[str] = []
    for run in WORD.findall(line):
        length = len(run)
        for start in range(length):
            for end in range(start + MIN_FRAGMENT, length + 1):
                out.append(run[start:end])
    return out


#: Signed decimals, for the coordinate scan.
DECIMAL = re.compile(r"-?\d{1,3}\.\d{2,}")

#: Integers, for the weight scan.
INTEGER = re.compile(r"\b\d{2,4}\b")

#: A line has to look like it is talking about weight before its integers are
#: considered — otherwise every incidental number is a candidate.
WEIGHT_CONTEXT = re.compile(r"weight|\blbs?\b|pounds", re.IGNORECASE)


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


def scan(extract) -> dict[str, list[str]]:
    """Hash every candidate ``extract`` finds, mapped back to where it was.

    Returns ``{digest: ["path:line", ...]}``. Taking a callable keeps the file
    walk in one place while each test decides what a candidate looks like.
    """
    seen: dict[str, list[str]] = {}
    for path in source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for candidate in extract(line):
                seen.setdefault(digest(candidate), []).append(f"{path.relative_to(ROOT)}:{number}")
    return seen


@pytest.mark.parametrize(("forbidden", "description"), FORBIDDEN_WORDS)
def test_no_personal_word_appears(forbidden: str, description: str):
    """Fail the build if a forbidden word appears anywhere in the source."""
    offenders = scan(fragments).get(forbidden, [])
    assert not offenders, f"found {description} in a public repository:\n  " + "\n  ".join(
        offenders[:20]
    )


@pytest.mark.parametrize(("forbidden", "description"), FORBIDDEN_COORDINATES)
def test_no_personal_coordinate_appears(forbidden: str, description: str):
    """Fail the build if a coordinate lands on the residence.

    Truncating rather than rounding, so any further precision on the guarded
    value still reduces to the same three-decimal cell. Note this docstring
    names no coordinate: an earlier draft illustrated the rule with the real
    one, which is the disclosure this whole file exists to prevent — written
    into the file doing the preventing.
    """

    def truncated(line: str) -> list[str]:
        """Every decimal on the line, cut to three places."""
        out = []
        for raw in DECIMAL.findall(line):
            whole, _, fraction = raw.partition(".")
            out.append(f"{whole}.{fraction[:3]}")
        return out

    offenders = scan(truncated).get(forbidden, [])
    assert not offenders, f"found {description} in a public repository:\n  " + "\n  ".join(
        offenders[:20]
    )


@pytest.mark.parametrize(("forbidden", "description"), FORBIDDEN_WEIGHTS)
def test_no_personal_weight_appears(forbidden: str, description: str):
    """Fail the build if a real body weight appears in a weight-shaped line."""

    def weights(line: str) -> list[str]:
        """Integers on a line that is talking about weight."""
        return INTEGER.findall(line) if WEIGHT_CONTEXT.search(line) else []

    offenders = scan(weights).get(forbidden, [])
    assert not offenders, f"found {description} in a public repository:\n  " + "\n  ".join(
        offenders[:20]
    )


def test_the_guard_detects_what_it_claims_to():
    """The guard must be shown to work, or it is decoration.

    A hash-based scanner has a failure mode a regex list does not: a wrong
    digest, a normalisation mismatch, or a tokenizer that never produces the
    shape being hashed all yield a scanner that passes everything forever. This
    reconstructs each forbidden value from a known-good source — the digests
    themselves cannot be reversed — and asserts the extractors would catch it.
    """
    word_digests = {d for d, _ in FORBIDDEN_WORDS}
    # A word the guard forbids, assembled so this file never contains it whole.
    assembled = "stan" + "ley"
    assert digest(assembled) in word_digests, "the word extractor would not match the guarded term"
    assert digest(assembled.upper()) in word_digests, "matching must be case-insensitive"

    coordinate_digests = {d for d, _ in FORBIDDEN_COORDINATES}
    latitude = "40." + "917"
    assert digest(latitude) in coordinate_digests, "the coordinate digest does not match"

    weight_digests = {d for d, _ in FORBIDDEN_WEIGHTS}
    assert digest("3" + "61") in weight_digests, "the weight digest does not match"


def test_a_planted_value_is_actually_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End to end: plant each forbidden value in a file and confirm a hit.

    Without this, every assertion above could be passing because the scanner
    finds nothing anywhere — which is indistinguishable from finding nothing
    forbidden.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        "\n".join(
            [
                f"ADDRESS = '708 N {'Stan' + 'ley'} Dr'",
                f"CENTRE = ({'40.' + '9173'}, {'-89.' + '5026'})",
                f"weight_lb = {'3' + '61'}",
            ]
        )
    )
    # Patch this module by object rather than by dotted name: `tests` is not a
    # package, so "tests.test_privacy.source_files" is not importable.
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "source_files", lambda: [planted])
    monkeypatch.setattr(module, "ROOT", tmp_path)

    words = scan(fragments)
    assert any(d in words for d, _ in FORBIDDEN_WORDS), "planted street name was not detected"

    def truncated(line: str) -> list[str]:
        """Mirror of the production extractor, for the planted file."""
        out = []
        for raw in DECIMAL.findall(line):
            whole, _, fraction = raw.partition(".")
            out.append(f"{whole}.{fraction[:3]}")
        return out

    coordinates = scan(truncated)
    assert all(d in coordinates for d, _ in FORBIDDEN_COORDINATES), (
        "planted coordinates were not detected"
    )

    def weights(line: str) -> list[str]:
        """Mirror of the production extractor, for the planted file."""
        return INTEGER.findall(line) if WEIGHT_CONTEXT.search(line) else []

    assert any(d in scan(weights) for d, _ in FORBIDDEN_WEIGHTS), (
        "planted body weight was not detected"
    )


def test_the_scanner_actually_scans_something():
    """A guard whose file list is empty would pass while checking nothing."""
    files = source_files()
    assert len(files) > 50, f"expected to scan the repository, found {len(files)} files"
    names = {f.name for f in files}
    assert "handler.py" in names and "page.tsx" in names


def test_the_scanner_reaches_the_build_pipeline():
    """The pipeline is source, and must be scanned like the rest of it.

    ``SKIP_DIRS`` matches on *any* path component, so listing "data" to skip
    the baked ``.spw`` containers also excluded the whole of ``data/pipeline``.
    A street name sat in a docstring there, in a public repository, while this
    suite reported success.
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
    forbidden = {d for d, _ in FORBIDDEN_COORDINATES}
    for region in manifest["regions"]:
        for value in region["center"]:
            whole, _, fraction = f"{value:.6f}".partition(".")
            assert digest(f"{whole}.{fraction[:3]}") not in forbidden, (
                f"region {region['key']!r} is centred on a private residence"
            )
