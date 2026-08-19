"""Geocoding against Overture's address theme.

There is no third-party geocoder here, on purpose. Overture ships ~395k
addresses for San Francisco and ~114k for the Peoria area, which is the same
corpus the commercial geocoders are partly built from — so resolving the user's
address from it keeps the app free, keeps their home address off other people's
servers, and makes the Overture dependency real rather than decorative.

The hard part is not the lookup, it is that people do not type addresses the way
datasets store them. Overture has "North Main Street"; a user types
"100 N Main St, Chillicothe IL". :func:`normalize_street` folds both to the
same key, and the number falls back to the nearest one on the street when the
exact house is missing — which it often is.
"""

from __future__ import annotations

import logging
import re

LOG = logging.getLogger(__name__)

# Both directions of the usual USPS abbreviations, folded to the long form.
_STREET_ABBREV = {
    "st": "street",
    "str": "street",
    "ave": "avenue",
    "av": "avenue",
    "blvd": "boulevard",
    "rd": "road",
    "dr": "drive",
    "ln": "lane",
    "ct": "court",
    "pl": "place",
    "ter": "terrace",
    "terr": "terrace",
    "pkwy": "parkway",
    "pky": "parkway",
    "hwy": "highway",
    "cir": "circle",
    "sq": "square",
    "aly": "alley",
    "wy": "way",
    "expy": "expressway",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
}

# Tokens that carry no matching signal once the street is isolated.
_NOISE = {"apt", "unit", "ste", "suite", "no", "number", "#"}

_STATE_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$", re.IGNORECASE)
_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
# The optional letter is a house-number suffix ("708A Market St") and must be
# *adjacent* to the digits. Allowing a space here would swallow the directional
# prefix of "100 N Main St" and search for "Main Street" instead.
_LEADING_NUMBER = re.compile(r"^\s*(\d+)([A-Za-z]?)(?![A-Za-z0-9])")
_LATLON = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def normalize_street(name: str) -> str:
    """Fold a street name to a canonical form for matching.

    Lowercases, strips punctuation, and expands directional and type
    abbreviations, so "N Main St", "North Main Street" and "n. main st"
    all collapse to ``north main street``.

    Used by both the offline builder (to key the index) and the runtime (to look
    into it), so it must stay a pure function of its input.
    """
    lowered = name.lower()
    # Apostrophes are *deleted* rather than turned into a space, so O'Farrell
    # and OFarrell reach the same key. Every other separator becomes a space,
    # so "St.Louis" still splits into two words.
    lowered = lowered.replace("'", "").replace("\u2019", "")
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    words = [_STREET_ABBREV.get(w, w) for w in cleaned.split() if w not in _NOISE]
    return " ".join(words)


class ParsedAddress:
    """The pieces of a typed address that matter for lookup."""

    __slots__ = ("number", "street", "street_norm", "postcode", "state", "raw")

    def __init__(
        self, raw: str, number: int | None, street: str, postcode: str | None, state: str | None
    ):
        self.raw = raw
        self.number = number
        self.street = street
        self.street_norm = normalize_street(street)
        self.postcode = postcode
        self.state = state

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ParsedAddress(number={self.number}, street={self.street!r}, "
            f"norm={self.street_norm!r}, postcode={self.postcode})"
        )


def parse_address(text: str) -> ParsedAddress:
    """Split free text into house number, street, and postal hints.

    Deliberately forgiving. Anything after the first comma is treated as
    city/state/zip context — useful for choosing a region, useless for matching
    the street — and a missing house number is fine, since a street-only query
    still resolves to the middle of that street.
    """
    raw = (text or "").strip()
    state = None
    postcode = None

    m = _STATE_ZIP.search(raw)
    if m:
        state, postcode = m.group(1).upper(), m.group(2)
    else:
        z = _ZIP.search(raw)
        if z:
            postcode = z.group(1)

    # The street lives in the first comma-delimited field; the rest is locality.
    head = raw.split(",")[0].strip()
    number: int | None = None
    nm = _LEADING_NUMBER.match(head)
    if nm:
        number = int(nm.group(1))
        head = head[nm.end() :].strip()
    else:
        # Some places write "Main St 708"; tolerate a trailing number too.
        tail = re.search(r"\b(\d{1,6})\s*$", head)
        if tail:
            number = int(tail.group(1))
            head = head[: tail.start()].strip()

    # Strip a trailing zip/state that leaked into the first field.
    head = _STATE_ZIP.sub("", head).strip()
    head = _ZIP.sub("", head).strip()

    parsed = ParsedAddress(raw, number, head, postcode, state)
    LOG.debug("parse_address %r -> %r", raw, parsed)
    return parsed


def parse_latlon(text: str) -> tuple[float, float] | None:
    """Accept a raw ``lat, lon`` pair so the map's "use this point" flow works."""
    m = _LATLON.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    return None


def geocode(index, text: str) -> dict:
    """Resolve free text to a point using one region's address index.

    Returns a dict with ``found`` plus, on success, the matched address and its
    coordinates. On failure it returns street suggestions rather than a bare
    "not found", because the usual cause is a spelling or a missing suffix.
    """
    coords = parse_latlon(text)
    if coords:
        lat, lon = coords
        LOG.debug("geocode: literal coordinates lat=%.5f lon=%.5f", lat, lon)
        return {
            "found": True,
            "match": "coordinates",
            "lat": lat,
            "lon": lon,
            "label": f"{lat:.5f}, {lon:.5f}",
        }

    parsed = parse_address(text)
    if not parsed.street_norm:
        return {"found": False, "reason": "no street name in query", "suggestions": []}

    if parsed.number is not None:
        hit = index.lookup(parsed.street_norm, parsed.number)
        if hit:
            label = f"{hit['number']} {hit['street']}"
            if hit["postcode"]:
                label += f", {hit['postcode']}"
            LOG.info(
                "geocode hit street=%r number=%s exact=%s",
                parsed.street_norm,
                parsed.number,
                hit["exact"],
            )
            return {
                "found": True,
                "match": "exact" if hit["exact"] else "nearest_number",
                "lat": hit["lat"],
                "lon": hit["lon"],
                "label": label,
                "address": hit,
            }

    # Street known but no usable number: fall back to a point on that street.
    rng = index.ranges.get(parsed.street_norm)
    if rng:
        lo, hi = rng
        mid = (lo + hi) // 2
        row = index._row(mid, exact=False)
        LOG.info("geocode street-only match street=%r", parsed.street_norm)
        return {
            "found": True,
            "match": "street_midpoint",
            "lat": row["lat"],
            "lon": row["lon"],
            "label": f"{row['street']} (no house number given)",
            "address": row,
        }

    suggestions = index.street_candidates(parsed.street_norm)
    LOG.info("geocode miss street=%r suggestions=%d", parsed.street_norm, len(suggestions))
    return {
        "found": False,
        "reason": f"no street matching {parsed.street!r} in this region",
        "suggestions": suggestions,
    }
