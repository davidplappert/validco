"""The geocoding corpus for one region.

Overture ships ~395k addresses for San Francisco and ~114k for the Peoria area.
They are baked sorted by normalised street name and then house number, so a
lookup is one dict hit and a binary search — nothing proportional to the corpus
size happens at request time, which is what keeps cold starts fast.
"""

from __future__ import annotations

import difflib
import logging
from bisect import bisect_left
from typing import Any

from ..container import Container

LOG = logging.getLogger(__name__)

#: Similarity threshold for "did you mean" street suggestions. Tuned by hand:
#: 0.72 catches a transposed or dropped letter without proposing streets that
#: merely share a suffix, which is worse than offering nothing.
FUZZY_CUTOFF = 0.72


class AddressIndex:
    """Street-and-number lookup over the baked Overture address corpus."""

    def __init__(self, container: Container):
        """Bind a container and decode the address columns."""
        self.container = container
        self.meta = container.meta
        self.streets: list[str] = self.meta["streets"]
        self.postcodes: list[str] = self.meta["postcodes"]
        self.ranges: dict[str, list[int]] = self.meta["street_ranges"]
        self.display: dict[str, str] = self.meta["street_display"]
        self.suffixes: dict[str, str] = self.meta["suffixes"]

        self.addr_num = container.get("addr_num")
        self.addr_lat = container.get("addr_lat")
        self.addr_lon = container.get("addr_lon")
        self.addr_street = container.get("addr_street")
        self.addr_post = container.get("addr_post")

        LOG.debug("AddressIndex ready count=%d streets=%d", len(self.addr_num), len(self.ranges))

    def __len__(self) -> int:
        """Number of addresses in this region."""
        return len(self.addr_num)

    def knows_street(self, street_norm: str) -> bool:
        """Whether a normalised street name exists in this region."""
        return street_norm in self.ranges

    def lookup(self, street_norm: str, number: int) -> dict[str, Any] | None:
        """Find a house number on a street, falling back to the nearest one.

        The fallback is deliberate. Address datasets always have holes, and
        placing someone at 706 when they typed 708 is a far better answer than
        "address not found" — the walk starts in the right place either way.
        The returned ``exact`` flag lets the caller say which happened.
        """
        span = self.ranges.get(street_norm)
        if span is None:
            return None
        lo, hi = span
        numbers = self.addr_num
        # The slice is sorted by construction, so the insertion point's two
        # neighbours bracket the answer.
        position = bisect_left(numbers, number, lo, hi)
        candidates = [p for p in (position - 1, position, position + 1) if lo <= p < hi]
        if not candidates:
            return None
        best = min(candidates, key=lambda p: abs(numbers[p] - number))
        return self.row(best, exact=numbers[best] == number)

    def midpoint_of(self, street_norm: str) -> dict[str, Any] | None:
        """A representative point on a street, when no house number was given."""
        span = self.ranges.get(street_norm)
        if span is None:
            return None
        lo, hi = span
        return self.row((lo + hi) // 2, exact=False)

    def row(self, index: int, exact: bool) -> dict[str, Any]:
        """Materialise one address row into a dict.

        House-number suffixes ("708A") are stored in a sparse side table rather
        than a per-row column, because well under one percent of addresses have
        one and a full column would cost more than the whole suffix map.
        """
        suffix = self.suffixes.get(str(index), "")
        return {
            "number": f"{self.addr_num[index]}{suffix}",
            "street": self.streets[self.addr_street[index]],
            "postcode": self.postcodes[self.addr_post[index]],
            "lat": self.addr_lat[index],
            "lon": self.addr_lon[index],
            "exact": exact,
        }

    def street_candidates(self, street_norm: str, limit: int = 5) -> list[str]:
        """Street names resembling a query, for a "did you mean" response.

        Ranked exact, then prefix, then substring, then fuzzy. A geocoding miss
        is almost always a typo or a missing suffix, so returning candidates
        turns a dead end into a correction the user can act on.

        The fuzzy pass is what rescues a genuine misspelling — "Californa St"
        finds "California Street", which neither prefix nor substring matching
        would. It runs only when the cheaper passes found nothing, because it
        scans the whole street dictionary; at a few thousand streets per region
        that is a couple of milliseconds, and only on a miss.
        """
        if not street_norm:
            return []
        exact = [s for s in self.ranges if s == street_norm]
        prefix = sorted(s for s in self.ranges if s.startswith(street_norm) and s not in exact)
        contains = sorted(
            s for s in self.ranges if street_norm in s and s not in exact and s not in prefix
        )
        ranked = exact + prefix + contains

        if not ranked:
            ranked = difflib.get_close_matches(
                street_norm, list(self.ranges), n=limit, cutoff=FUZZY_CUTOFF
            )
            LOG.debug("fuzzy street match query=%r hits=%d", street_norm, len(ranked))

        return [self.display.get(s, s) for s in ranked[:limit]]
