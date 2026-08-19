"""Resolving what someone typed into a point on the map.

No third-party geocoder, on purpose: Overture's address theme is the same corpus
the commercial ones are partly built from, so using it keeps the app free, keeps
the user's home address off other people's servers, and makes the Overture
dependency real rather than decorative.

The hard part is not the lookup, it is that people do not type addresses the way
datasets store them. Overture has "North Main Street" where a user types "100 N Main St", and is not
even self-consistent between regions: Peoria spells numbered streets out
("North SECOND Street") while San Francisco zero-pads them ("03RD ST").
"""

from __future__ import annotations

import logging
import re

from ..models.location import Coordinate, GeocodeResult

LOG = logging.getLogger(__name__)


class StreetNormalizer:
    """Folds street names to a canonical matching key.

    Used by both the offline builder — which keys the index by its output — and
    the runtime, which looks into that index. It must therefore stay a pure
    function of its input, and **changing it requires rebuilding the address
    containers**, or every lookup silently misses.
    """

    #: USPS abbreviations, folded to the long form in both directions.
    ABBREVIATIONS = {
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

    #: Spelled-out ordinals, mapped to their numeric form.
    #:
    #: Overture is not internally consistent about numbered streets, which are
    #: among the most common street names in North America. Peoria's data
    #: spells them out — "North SECOND Street" — while San Francisco's
    #: zero-pads the digits — "03RD ST". A user types neither; they type
    #: "2nd St" and "3rd St".
    #:
    #: All three forms are folded to one canonical key: the unpadded numeric
    #: ordinal, "2nd" and "3rd". Numeric is the right canonical form because it
    #: extends to any number, whereas a word table stops wherever it was
    #: written out to.
    ORDINAL_WORDS = {
        "first": "1st",
        "second": "2nd",
        "third": "3rd",
        "fourth": "4th",
        "fifth": "5th",
        "sixth": "6th",
        "seventh": "7th",
        "eighth": "8th",
        "ninth": "9th",
        "tenth": "10th",
        "eleventh": "11th",
        "twelfth": "12th",
        "thirteenth": "13th",
        "fourteenth": "14th",
        "fifteenth": "15th",
        "sixteenth": "16th",
        "seventeenth": "17th",
        "eighteenth": "18th",
        "nineteenth": "19th",
        "twentieth": "20th",
        "thirtieth": "30th",
        "fortieth": "40th",
        "fiftieth": "50th",
    }

    #: A zero-padded numeric ordinal, as San Francisco's data writes them.
    PADDED_ORDINAL = re.compile(r"^0+(\d+(?:st|nd|rd|th))$")

    #: Tokens carrying no matching signal once the street is isolated.
    NOISE = {"apt", "unit", "ste", "suite", "no", "number", "#"}

    def normalize(self, name: str) -> str:
        """Fold a street name to its canonical form.

        Apostrophes are *deleted* rather than replaced with a space, so
        O'Farrell and OFarrell reach the same key. Every other separator becomes
        a space, so "St.Louis" still splits into two words. Numbered streets fold to
        a canonical numeric ordinal, so "2nd St", "Second St" and "02nd St" all
        reach the same key.
        """
        lowered = name.lower().replace("'", "").replace("’", "")
        cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
        words = []
        for word in cleaned.split():
            if word in self.NOISE:
                continue
            word = self._canonical_ordinal(word)
            words.append(self.ABBREVIATIONS.get(word, word))
        return " ".join(words)

    def _canonical_ordinal(self, word: str) -> str:
        """Fold any spelling of a numbered street to its canonical form.

        "second", "02nd" and "2nd" all become "2nd". Anything that is not an
        ordinal passes through untouched.
        """
        if word in self.ORDINAL_WORDS:
            return self.ORDINAL_WORDS[word]
        padded = self.PADDED_ORDINAL.match(word)
        return padded.group(1) if padded else word


class ParsedAddress:
    """The pieces of a typed address that matter for lookup."""

    __slots__ = ("raw", "number", "street", "street_norm", "postcode", "state")

    def __init__(
        self,
        raw: str,
        number: int | None,
        street: str,
        street_norm: str,
        postcode: str | None,
        state: str | None,
    ):
        """Store the parsed components alongside the original text."""
        self.raw = raw
        self.number = number
        self.street = street
        self.street_norm = street_norm
        self.postcode = postcode
        self.state = state

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return (
            f"ParsedAddress(number={self.number}, street={self.street!r}, "
            f"norm={self.street_norm!r}, postcode={self.postcode})"
        )


class AddressParser:
    """Splits free text into a house number, a street and postal hints.

    Deliberately forgiving. Anything after the first comma is locality context —
    useful for picking a region, useless for matching a street — and a missing
    house number still resolves, to a point on the street.
    """

    STATE_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})(?:-\d{4})?\s*$", re.IGNORECASE)
    ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
    TRAILING_NUMBER = re.compile(r"\b(\d{1,6})\s*$")
    #: The optional letter is a house-number suffix ("708A Market St") and must
    #: be *adjacent* to the digits. Allowing a space would swallow the
    #: directional prefix of "100 N Main St" and search for "Main Street".
    LEADING_NUMBER = re.compile(r"^\s*(\d+)([A-Za-z]?)(?![A-Za-z0-9])")

    LATLON = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

    def __init__(self, normalizer: StreetNormalizer | None = None):
        """Bind a street normalizer, defaulting to the standard one."""
        self.normalizer = normalizer or StreetNormalizer()

    def parse(self, text: str) -> ParsedAddress:
        """Parse free text into a :class:`ParsedAddress`."""
        raw = (text or "").strip()
        state, postcode = None, None

        match = self.STATE_ZIP.search(raw)
        if match:
            state, postcode = match.group(1).upper(), match.group(2)
        else:
            zip_match = self.ZIP.search(raw)
            if zip_match:
                postcode = zip_match.group(1)

        head = raw.split(",")[0].strip()
        number: int | None = None

        leading = self.LEADING_NUMBER.match(head)
        if leading:
            number = int(leading.group(1))
            head = head[leading.end() :].strip()
        else:
            # Some places write "Main St 100"; tolerate that too.
            trailing = self.TRAILING_NUMBER.search(head)
            if trailing:
                number = int(trailing.group(1))
                head = head[: trailing.start()].strip()

        # Strip a zip or state that leaked into the first comma field.
        head = self.ZIP.sub("", self.STATE_ZIP.sub("", head)).strip()

        parsed = ParsedAddress(raw, number, head, self.normalizer.normalize(head), postcode, state)
        LOG.debug("parsed address %r -> %r", raw, parsed)
        return parsed

    def parse_latlon(self, text: str) -> Coordinate | None:
        """Accept a raw ``lat, lon`` pair, so "use this point" round-trips."""
        match = self.LATLON.match(text or "")
        if not match:
            return None
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return Coordinate(lat, lon)
        return None


class Geocoder:
    """Resolves free text against one region's address index."""

    def __init__(self, index, parser: AddressParser | None = None):
        """Bind an address index and, optionally, a custom parser."""
        self.index = index
        self.parser = parser or AddressParser()

    def resolve(self, text: str) -> GeocodeResult:
        """Turn free text into a :class:`GeocodeResult`.

        Tries, in order: literal coordinates, house number on a street, and a
        point on the street when no usable number was given. A miss carries
        street suggestions rather than a bare failure, because the usual cause
        is a spelling or a missing suffix.
        """
        coordinate = self.parser.parse_latlon(text)
        if coordinate:
            LOG.debug("geocode: literal coordinates %s", coordinate)
            return GeocodeResult.hit(
                coordinate, f"{coordinate.lat:.5f}, {coordinate.lon:.5f}", "coordinates"
            )

        parsed = self.parser.parse(text)
        if not parsed.street_norm:
            return GeocodeResult.miss("no street name in query")

        if parsed.number is not None:
            row = self.index.lookup(parsed.street_norm, parsed.number)
            if row:
                LOG.info(
                    "geocode hit street=%r number=%s exact=%s",
                    parsed.street_norm,
                    parsed.number,
                    row["exact"],
                )
                return GeocodeResult.hit(
                    Coordinate(row["lat"], row["lon"]),
                    self._label(row),
                    "exact" if row["exact"] else "nearest_number",
                    address=row,
                )

        row = self.index.midpoint_of(parsed.street_norm)
        if row:
            LOG.info("geocode street-only match street=%r", parsed.street_norm)
            return GeocodeResult.hit(
                Coordinate(row["lat"], row["lon"]),
                f"{row['street']} (no house number given)",
                "street_midpoint",
                address=row,
            )

        suggestions = self.index.street_candidates(parsed.street_norm)
        LOG.info("geocode miss street=%r suggestions=%d", parsed.street_norm, len(suggestions))
        return GeocodeResult.miss(
            f"no street matching {parsed.street!r} in this region", suggestions
        )

    @staticmethod
    def _label(row: dict) -> str:
        """Human-readable label for a matched address row."""
        label = f"{row['number']} {row['street']}"
        return f"{label}, {row['postcode']}" if row["postcode"] else label
