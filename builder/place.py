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

#: ISO 3166-1 alpha-2 codes and the English names people type for them.
#:
#: Held as a compact literal rather than a two-hundred-line dictionary because
#: these names are data, not logic, and spelling them out one per line would
#: bury the twenty lines of parsing this module actually does.
#:
#: Format: ``CODE name[,alias,alias]``, one country per semicolon-separated
#: entry. Only sovereign states and the territories Overture keys separately are
#: listed; anything missing simply falls through to the candidate search.
_COUNTRY_LITERAL = """
AD andorra; AE united arab emirates,uae; AF afghanistan; AG antigua and barbuda;
AL albania; AM armenia; AO angola; AR argentina; AT austria; AU australia;
AZ azerbaijan; BA bosnia and herzegovina,bosnia; BB barbados; BD bangladesh;
BE belgium; BF burkina faso; BG bulgaria; BH bahrain; BI burundi; BJ benin;
BN brunei; BO bolivia; BR brazil; BS bahamas,the bahamas; BT bhutan; BW botswana;
BY belarus; BZ belize; CA canada; CD democratic republic of the congo,drc;
CF central african republic; CG republic of the congo,congo; CH switzerland;
CI ivory coast,cote d'ivoire; CL chile; CM cameroon; CN china; CO colombia;
CR costa rica; CU cuba; CV cape verde; CY cyprus; CZ czechia,czech republic;
DE germany; DJ djibouti; DK denmark; DM dominica; DO dominican republic;
DZ algeria; EC ecuador; EE estonia; EG egypt; ER eritrea; ES spain; ET ethiopia;
FI finland; FJ fiji; FM micronesia; FR france; GA gabon;
GB united kingdom,uk,great britain,britain,england,scotland,wales;
GD grenada; GE georgia; GH ghana; GM gambia; GN guinea; GQ equatorial guinea;
GR greece; GT guatemala; GW guinea-bissau; GY guyana; HK hong kong; HN honduras;
HR croatia; HT haiti; HU hungary; ID indonesia; IE ireland; IL israel; IN india;
IQ iraq; IR iran; IS iceland; IT italy; JM jamaica; JO jordan; JP japan;
KE kenya; KG kyrgyzstan; KH cambodia; KI kiribati; KM comoros; KN saint kitts and nevis;
KP north korea; KR south korea,korea; KW kuwait; KZ kazakhstan; LA laos;
LB lebanon; LC saint lucia; LI liechtenstein; LK sri lanka; LR liberia;
LS lesotho; LT lithuania; LU luxembourg; LV latvia; LY libya; MA morocco;
MC monaco; MD moldova; ME montenegro; MG madagascar; MH marshall islands;
MK north macedonia,macedonia; ML mali; MM myanmar,burma; MN mongolia; MO macau;
MR mauritania; MT malta; MU mauritius; MV maldives; MW malawi; MX mexico;
MY malaysia; MZ mozambique; NA namibia; NE niger; NG nigeria; NI nicaragua;
NL netherlands,the netherlands,holland; NO norway; NP nepal; NR nauru;
NZ new zealand; OM oman; PA panama; PE peru; PG papua new guinea;
PH philippines,the philippines; PK pakistan; PL poland; PR puerto rico;
PT portugal; PW palau; PY paraguay; QA qatar; RO romania; RS serbia; RU russia;
RW rwanda; SA saudi arabia; SB solomon islands; SC seychelles; SD sudan;
SE sweden; SG singapore; SI slovenia; SK slovakia; SL sierra leone; SM san marino;
SN senegal; SO somalia; SR suriname; SS south sudan; ST sao tome and principe;
SV el salvador; SY syria; SZ eswatini,swaziland; TD chad; TG togo; TH thailand;
TJ tajikistan; TL timor-leste,east timor; TM turkmenistan; TN tunisia; TO tonga;
TR turkey,turkiye; TT trinidad and tobago; TV tuvalu; TW taiwan; TZ tanzania;
UA ukraine; UG uganda; UY uruguay; UZ uzbekistan; VA vatican city;
VC saint vincent and the grenadines;
VE venezuela; VN vietnam; VU vanuatu; WS samoa; YE yemen; ZA south africa;
ZM zambia; ZW zimbabwe
"""


def _country_index() -> dict[str, str]:
    """Expand the compact country literal into a name/code lookup."""
    index: dict[str, str] = {}
    for entry in _COUNTRY_LITERAL.replace("\n", " ").split(";"):
        entry = entry.strip()
        if not entry:
            continue
        code, _, names = entry.partition(" ")
        index[code.lower()] = code
        for name in names.split(","):
            if name.strip():
                index[name.strip()] = code
    return index


_COUNTRIES = _country_index()

#: Ways of writing the United States. Kept separate because they resolve to a
#: country *and* leave the state slot open for the field before them.
_US_ALIASES = ("us", "usa", "united states", "united states of america", "america")

_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_STREET_NUMBER = re.compile(r"^\s*\d+[A-Za-z]?\s+")


class ParsedPlace:
    """A place query split into the parts that narrow a divisions lookup.

    ``candidates`` holds every field that could be the locality, best guess
    first; ``name`` is simply the first of them. See :meth:`PlaceParser.parse`
    for why one guess is not enough.
    """

    __slots__ = ("raw", "candidates", "state", "country")

    def __init__(self, raw: str, candidates: list[str], state: str | None, country: str | None):
        """Store the query alongside what could be extracted from it."""
        self.raw = raw
        self.candidates = candidates
        self.state = state
        self.country = country

    @property
    def name(self) -> str:
        """The most likely locality — the first candidate, or empty."""
        return self.candidates[0] if self.candidates else ""

    def __repr__(self) -> str:
        """Compact representation for logs and test failures."""
        return (
            f"ParsedPlace(name={self.name!r}, state={self.state!r}, "
            f"country={self.country!r}, candidates={self.candidates!r})"
        )


class PlaceParser:
    """Splits a free-text place or address into name, state and country.

    Tolerant on purpose: users type "Austin", "Austin, TX", "Austin, Texas,
    USA", and — because the same box accepts addresses — "1600 Amphitheatre
    Pkwy, Mountain View, CA". All of those should find the right city.
    """

    def parse(self, query: str) -> ParsedPlace:
        """Extract the locality candidates and any state or country qualifier.

        The last comma-separated field is the best guess at the locality,
        because an address reads "street, city" and a bare place reads just
        "city". "Paris, France" reads the same way and its last field is a
        country, so trailing countries are recognised and stripped first, and
        whatever is left is kept as an *ordered list* of candidates rather than
        a single answer.

        Both halves are load-bearing, and each was a real wrong answer:

        * Without the country table, "Kyoto, Japan" matches the locality
          genuinely named **Japan** in East Java, exactly.
        * Without the candidate list, a country the table does not know sends
          the search after a locality by that name — which is how "Paris,
          France" once resolved, via the prefix fallback, to **Frances, South
          Australia**.

        States are matched before two-letter country codes on purpose: ``IL``
        is both Illinois and Israel, and ``IN`` both Indiana and India. For a
        product whose coverage starts in the United States, the state reading
        is the right default.
        """
        raw = (query or "").strip()
        cleaned = _ZIP.sub("", raw).strip().strip(",")

        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        country: str | None = None
        state: str | None = None

        # A trailing "USA", which still leaves a state to find in front of it.
        if parts and parts[-1].lower() in _US_ALIASES:
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

        # A trailing country anywhere else in the world. Only consulted when no
        # state was found, so "Springfield, IL" stays in Illinois.
        if parts and state is None and country is None:
            tail = parts[-1].lower().strip(".")
            if tail in _COUNTRIES:
                country = _COUNTRIES[tail]
                parts.pop()

        # Reversed, so the last field is tried first. Each candidate loses any
        # leading house number, so "100 N Main St" does not become a locality
        # search for a street.
        candidates: list[str] = []
        for part in reversed(parts or [cleaned]):
            candidate = _STREET_NUMBER.sub("", part).strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        parsed = ParsedPlace(raw, candidates, state, country)
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

        Matching is case-insensitive. Every locality candidate is tried as an
        exact name first, in order of likelihood; only when all of them miss
        does a prefix match on the best guess get a look in. That ordering is
        load-bearing — an exact match on a *later* candidate is far better
        evidence than a fuzzy match on the first one, which is how "Paris,
        France" used to come back as "Frances, South Australia".

        Results are ordered by subtype preference and then by area, so "Austin"
        in Texas beats the several much smaller Austins elsewhere.
        """
        parsed = self.parser.parse(query)
        if not parsed.candidates:
            return None

        subtypes = ", ".join(f"'{s}'" for s in PLACE_SUBTYPES)
        for candidate in parsed.candidates:
            rows = self._exact_search(candidate, parsed, subtypes)
            if rows:
                return self._describe(rows[0])

        LOG.info("no exact division match for %r, trying prefix", parsed.name)
        rows = self._prefix_search(parsed, subtypes)
        if not rows:
            return None
        return self._describe(rows[0])

    def _exact_search(self, candidate: str, parsed: ParsedPlace, subtypes: str) -> list:
        """Look one locality name up exactly, within any state or country given.

        Accents are folded on both sides, because Overture stores the endonym —
        "Zürich", "Malmö", "São Paulo" — and nobody types the diaeresis. The
        folded comparison rides along in the same query rather than running as a
        second pass: there is no index to exploit either way, so a second
        attempt would mean scanning the whole divisions theme twice.
        """
        filters = [
            "(lower(names.primary) = lower(?)"
            " OR lower(strip_accents(names.primary)) = lower(strip_accents(?)))"
        ]
        params: list[str] = [candidate, candidate]
        if parsed.country:
            filters.append("country = ?")
            params.append(parsed.country)
        if parsed.state:
            filters.append("region = ?")
            params.append(f"{parsed.country or 'US'}-{parsed.state}")

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
        return self.con.execute(sql, params).fetchall()

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
