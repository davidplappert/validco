"""Resolving a place name or a coordinate to a bounding box.

Uses Overture's own ``divisions`` theme rather than an external geocoder, which
keeps the whole product on one open dataset and means no API keys, no rate
limits and no third party learning where users live.

The surprising part is that this is fast. There is no bounding box to prune on
when searching by name, so DuckDB scans the divisions theme in full — but
divisions is small next to places or transportation, and a planet-wide lookup
returns in about three seconds.
"""

from __future__ import annotations

import logging
import re

LOG = logging.getLogger(__name__)

#: Division subtypes that correspond to somewhere a person would say they live.
#: Ordered by preference: a locality is a better answer than the county holding it.
PLACE_SUBTYPES = ("locality", "localadmin", "county", "region")

#: US state abbreviations, so "Austin, TX" narrows to Texas rather than
#: returning the largest Austin on the planet.
_US_STATES = {
    "al": "AL",
    "ak": "AK",
    "az": "AZ",
    "ar": "AR",
    "ca": "CA",
    "co": "CO",
    "ct": "CT",
    "de": "DE",
    "fl": "FL",
    "ga": "GA",
    "hi": "HI",
    "id": "ID",
    "il": "IL",
    "in": "IN",
    "ia": "IA",
    "ks": "KS",
    "ky": "KY",
    "la": "LA",
    "me": "ME",
    "md": "MD",
    "ma": "MA",
    "mi": "MI",
    "mn": "MN",
    "ms": "MS",
    "mo": "MO",
    "mt": "MT",
    "ne": "NE",
    "nv": "NV",
    "nh": "NH",
    "nj": "NJ",
    "nm": "NM",
    "ny": "NY",
    "nc": "NC",
    "nd": "ND",
    "oh": "OH",
    "ok": "OK",
    "or": "OR",
    "pa": "PA",
    "ri": "RI",
    "sc": "SC",
    "sd": "SD",
    "tn": "TN",
    "tx": "TX",
    "ut": "UT",
    "vt": "VT",
    "va": "VA",
    "wa": "WA",
    "wv": "WV",
    "wi": "WI",
    "wy": "WY",
    "dc": "DC",
}

_FULL_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}

_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_STREET_NUMBER = re.compile(r"^\s*\d+[A-Za-z]?\s+")


class ParsedPlace:
    """A place query split into the parts that narrow a divisions lookup."""

    __slots__ = ("raw", "name", "state", "country")

    def __init__(self, raw: str, name: str, state: str | None, country: str | None):
        """Store the query alongside what could be extracted from it."""
        self.raw = raw
        self.name = name
        self.state = state
        self.country = country

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return f"ParsedPlace(name={self.name!r}, state={self.state!r}, country={self.country!r})"


class PlaceParser:
    """Splits a free-text place or address into name, state and country.

    Tolerant on purpose: users type "Austin", "Austin, TX", "Austin, Texas,
    USA", and — because the same box accepts addresses — "1600 Amphitheatre
    Pkwy, Mountain View, CA". All of those should find the right city.
    """

    def parse(self, query: str) -> ParsedPlace:
        """Extract the locality and any state or country qualifier."""
        raw = (query or "").strip()
        cleaned = _ZIP.sub("", raw).strip().strip(",")

        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        country: str | None = None
        state: str | None = None

        # A trailing country, if it is one we can recognise.
        if parts and parts[-1].lower() in (
            "us",
            "usa",
            "united states",
            "united states of america",
        ):
            country = "US"
            parts.pop()

        # A trailing state, either abbreviated or spelled out.
        if parts:
            tail = parts[-1].lower().strip(".")
            if tail in _US_STATES:
                state, country = _US_STATES[tail], country or "US"
                parts.pop()
            elif tail in _FULL_STATE_NAMES:
                state, country = _FULL_STATE_NAMES[tail], country or "US"
                parts.pop()

        # Whatever remains: the last field is the locality, because an address
        # reads "street, city" and a bare place reads just "city".
        name = parts[-1] if parts else cleaned
        # Drop a leading house number, so "100 N Main St" does not become a
        # locality search for a street.
        name = _STREET_NUMBER.sub("", name).strip()

        parsed = ParsedPlace(raw, name, state, country)
        LOG.info("parsed place query=%r -> %r", raw, parsed)
        return parsed


class PlaceResolver:
    """Finds a bounding box for a place, using Overture divisions."""

    def __init__(self, connection, release_url: str):
        """Bind a DuckDB connection and the Overture release to search."""
        self.con = connection
        self.divisions = f"{release_url}/theme=divisions/type=division_area/*.parquet"
        self.parser = PlaceParser()

    def resolve_name(self, query: str) -> dict | None:
        """Find the best-matching division for a place name.

        Matching is case-insensitive and tries an exact name first, then a
        prefix. Results are ordered by subtype preference and then by area, so
        "Austin" in Texas beats the several much smaller Austins elsewhere.
        """
        parsed = self.parser.parse(query)
        if not parsed.name:
            return None

        filters = ["lower(names.primary) = lower(?)"]
        params: list[str] = [parsed.name]
        if parsed.country:
            filters.append("country = ?")
            params.append(parsed.country)
        if parsed.state:
            filters.append("region = ?")
            params.append(f"{parsed.country or 'US'}-{parsed.state}")

        subtypes = ", ".join(f"'{s}'" for s in PLACE_SUBTYPES)
        # `array_position` orders by how specific the subtype is, so a locality
        # is preferred over the county that contains it.
        sql = f"""
        SELECT names.primary AS name, subtype, country, region,
               bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax
        FROM read_parquet('{self.divisions}', hive_partitioning=1)
        WHERE subtype IN ({subtypes}) AND {" AND ".join(filters)}
        ORDER BY array_position([{subtypes}], subtype),
                 (bbox.xmax - bbox.xmin) * (bbox.ymax - bbox.ymin) DESC
        LIMIT 1
        """
        rows = self.con.execute(sql, params).fetchall()

        if not rows:
            LOG.info("no exact division match for %r, trying prefix", parsed.name)
            rows = self._prefix_search(parsed, subtypes)
        if not rows:
            return None
        return self._describe(rows[0])

    def _prefix_search(self, parsed: ParsedPlace, subtypes: str) -> list:
        """Fall back to a prefix match, for partial or misspelt input."""
        filters = ["lower(names.primary) LIKE lower(?)"]
        params: list[str] = [f"{parsed.name}%"]
        if parsed.country:
            filters.append("country = ?")
            params.append(parsed.country)

        sql = f"""
        SELECT names.primary AS name, subtype, country, region,
               bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax
        FROM read_parquet('{self.divisions}', hive_partitioning=1)
        WHERE subtype IN ({subtypes}) AND {" AND ".join(filters)}
        ORDER BY array_position([{subtypes}], subtype),
                 (bbox.xmax - bbox.xmin) * (bbox.ymax - bbox.ymin) DESC
        LIMIT 1
        """
        return self.con.execute(sql, params).fetchall()

    def resolve_point(self, lat: float, lon: float) -> dict | None:
        """Find the division containing a coordinate.

        Unlike the name search this *can* prune on the bounding box, so it only
        touches row groups near the point and returns quickly.
        """
        subtypes = ", ".join(f"'{s}'" for s in PLACE_SUBTYPES)
        sql = f"""
        SELECT names.primary AS name, subtype, country, region,
               bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax
        FROM read_parquet('{self.divisions}', hive_partitioning=1)
        WHERE subtype IN ({subtypes})
          AND bbox.xmin <= ? AND bbox.xmax >= ?
          AND bbox.ymin <= ? AND bbox.ymax >= ?
        ORDER BY array_position([{subtypes}], subtype),
                 (bbox.xmax - bbox.xmin) * (bbox.ymax - bbox.ymin) ASC
        LIMIT 1
        """
        rows = self.con.execute(sql, [lon, lon, lat, lat]).fetchall()
        if not rows:
            return None
        return self._describe(rows[0])

    @staticmethod
    def _describe(row) -> dict:
        """Turn a division row into a label and a bounding box."""
        name, subtype, country, region, xmin, ymin, xmax, ymax = row
        label_parts = [name]
        if region:
            # "US-TX" reads better as "TX".
            label_parts.append(region.split("-")[-1])
        elif country:
            label_parts.append(country)
        return {
            "name": name,
            "label": ", ".join(label_parts),
            "subtype": subtype,
            "country": country,
            "region": region,
            "bbox": [float(xmin), float(ymin), float(xmax), float(ymax)],
        }
