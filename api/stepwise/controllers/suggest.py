"""Address autocomplete, served from the same Overture corpus as the geocoder.

Typing a full address on a phone is the worst part of using this app, and every
commercial autocomplete (Google Places, Mapbox Search, Algolia) costs money per
keystroke and sends the user's partial home address to a third party.

Neither is necessary. The address index is already in memory: ~395k rows for San
Francisco keyed by normalised street name, with each street's row range
precomputed. Suggesting completions is a prefix scan over a few thousand street
names plus a bisect for house numbers — well under a millisecond, and the data
never leaves this Lambda.

The endpoint is deliberately forgiving about how much the user has typed:

* ``"cali"``            -> street names beginning with California
* ``"1100 cal"``        -> full addresses at 1100 on those streets
* ``"1100 california"`` -> the exact address, plus near numbers on that street
"""

from __future__ import annotations

import logging

from ..http.request import Request
from ..http.response import Response
from ..services.geocoder import AddressParser
from .base import Controller
from .system import ATTRIBUTION

LOG = logging.getLogger(__name__)

#: Longest query worth acting on. Beyond this the user has typed a full address
#: and should just submit it.
MAX_QUERY_CHARS = 80

#: Below this, almost every street in the region matches and the list is noise.
MIN_QUERY_CHARS = 2

#: Hard cap on returned suggestions, regardless of what the caller asks for.
MAX_RESULTS = 10

#: How many house numbers to offer per matching street when a number was typed.
#: More than a couple turns one street into a wall of near-identical options.
NUMBERS_PER_STREET = 2


class SuggestController(Controller):
    """``GET /v1/suggest?q=...`` — address completions as the user types."""

    def handle(self, request: Request) -> Response:
        """Return ranked address or street completions for a partial query.

        Always 200, even for a query too short to act on: an autocomplete that
        returns 4xx while someone is mid-word turns every keystroke into an
        error in the console and, worse, into an error in the UI.
        """
        query = (request.query.get("q") or "").strip()
        limit = request.integer(request.query.get("limit"), "limit", 1, MAX_RESULTS, 8)

        if len(query) < MIN_QUERY_CHARS or len(query) > MAX_QUERY_CHARS:
            return Response.ok({"query": query, "suggestions": []})

        keys = [request.query["region"]] if request.query.get("region") else self.registry.keys()

        suggestions: list[dict] = []
        for key in keys:
            if len(suggestions) >= limit:
                break
            suggestions.extend(self._for_region(key, query, limit - len(suggestions)))

        LOG.debug("suggest q=%r regions=%s hits=%d", query, keys, len(suggestions))
        return Response.ok(
            {
                "query": query,
                "suggestions": suggestions[:limit],
                "attribution": ATTRIBUTION,
            }
        )

    def _for_region(self, region_key: str, query: str, limit: int) -> list[dict]:
        """Completions from one region's address index."""
        index = self.registry.datasets(region_key).addresses
        parsed = AddressParser().parse(query)
        if not parsed.street_norm:
            return []

        streets = self._matching_streets(index, parsed.street_norm, limit)
        if parsed.number is None:
            return [self._street_suggestion(index, region_key, s) for s in streets]

        found: list[dict] = []
        for street in streets:
            found.extend(self._address_suggestions(index, region_key, street, parsed.number))
            if len(found) >= limit:
                break
        return found[:limit]

    @staticmethod
    def _matching_streets(index, street_norm: str, limit: int) -> list[str]:
        """Street keys matching a partial name, best first.

        Ranking is the whole difficulty. Sorting alphabetically or by length
        gives "cal" -> Calgary, Callippe, Calhoun, and buries California Street
        — which is certainly what was meant. There is no popularity data in
        Overture, but **the number of addresses on a street is a good proxy for
        how major it is**: California Street has thousands, Calgary Street has
        a handful. Ranking on that puts the intended answer first.

        The ordering is therefore: prefix matches before contained ones (that
        is what typing forwards implies), then by address count descending,
        then by name length so "Market Street" beats "Market Street Extension".

        Matching also considers the *display* name, not just the normalised
        key. The normaliser folds "Second" to "2nd", so a user midway through
        typing "908 N Sec..." produces "north sec", which prefix-matches
        nothing in the key space but matches the display name fine.
        """
        query = street_norm
        matches: list[tuple[int, int, int, str]] = []

        for key, (lo, hi) in index.ranges.items():
            display = index.display.get(key, key).lower()
            if key.startswith(query) or display.startswith(query):
                rank = 0
            elif query in key or query in display:
                rank = 1
            else:
                continue
            matches.append((rank, -(hi - lo), len(key), key))

        matches.sort()
        # A generous multiplier: several streets may yield no usable house
        # numbers, so gather more candidates than the caller asked for.
        return [key for _, _, _, key in matches[: limit * 3]]

    @staticmethod
    def _street_suggestion(index, region_key: str, street_norm: str) -> dict:
        """A street-level completion, positioned at the middle of the street."""
        row = index.midpoint_of(street_norm)
        display = index.display.get(street_norm, street_norm)
        return {
            "kind": "street",
            "label": display,
            "value": display,
            "region": region_key,
            "lat": row["lat"] if row else None,
            "lon": row["lon"] if row else None,
        }

    @staticmethod
    def _address_suggestions(index, region_key: str, street_norm: str, number: int) -> list[dict]:
        """Concrete addresses on one street, nearest to the typed number.

        Walks outward from the bisect position so a partially-typed number
        ("11" on California) offers the closest real house numbers rather than
        the numerically smallest ones on the street.
        """
        span = index.ranges.get(street_norm)
        if span is None:
            return []
        lo, hi = span

        from bisect import bisect_left

        position = bisect_left(index.addr_num, number, lo, hi)
        candidates = sorted(
            {
                p
                for p in range(position - NUMBERS_PER_STREET, position + NUMBERS_PER_STREET + 1)
                if lo <= p < hi
            },
            key=lambda p: (abs(index.addr_num[p] - number), index.addr_num[p]),
        )[:NUMBERS_PER_STREET]

        out = []
        for candidate in candidates:
            row = index.row(candidate, exact=index.addr_num[candidate] == number)
            label = f"{row['number']} {row['street']}"
            if row["postcode"]:
                label += f", {row['postcode']}"
            out.append(
                {
                    "kind": "address",
                    "label": label,
                    "value": label,
                    "region": region_key,
                    "lat": row["lat"],
                    "lon": row["lon"],
                }
            )
        return out
