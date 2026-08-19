"""Tests for address autocomplete.

Autocomplete lives or dies on ranking. A list that is technically correct but
puts the obvious answer fourth is worse than no list, because the user reads
three wrong options before finding theirs. Most of these tests are therefore
about *order*, not membership.
"""

from __future__ import annotations

import json
import time

import pytest
from stepwise.handler import handler


def suggest(query: str, limit: int = 8, region: str | None = None) -> dict:
    """Call the endpoint the way a browser would."""
    params = {"q": query, "limit": str(limit)}
    if region:
        params["region"] = region
    event = {
        "rawPath": "/v1/suggest",
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": params,
    }
    response = handler(event, None)
    assert response["statusCode"] == 200
    return json.loads(response["body"])


def labels(query: str, **kwargs) -> list[str]:
    """Just the display labels, for readable assertions."""
    return [s["label"] for s in suggest(query, **kwargs)["suggestions"]]


class TestRanking:
    def test_a_major_street_outranks_obscure_ones_sharing_a_prefix(self):
        """The test that motivated the ranking rewrite.

        Sorted alphabetically or by length, "cal" returns Calgary, Caledonia
        and Calhoun, and buries California Street — which is obviously what was
        meant. Address count stands in for prominence and fixes it.
        """
        assert labels("cal")[0].upper().startswith("CALIFORNIA")

    def test_ranking_holds_as_more_is_typed(self):
        for query in ("cal", "cali", "califo", "california"):
            assert labels(query)[0].upper().startswith("CALIFORNIA"), query

    def test_a_typed_house_number_produces_that_address_first(self):
        assert labels("1100 california")[0].startswith("1100 CALIFORNIA ST")

    def test_a_partial_street_with_a_number_still_finds_the_address(self):
        assert labels("1100 cal")[0].startswith("1100 CALIFORNIA ST")


class TestPartialWords:
    def test_a_partial_ordinal_word_matches(self, registry):
        """The normaliser folds "Second" to "2nd", so a half-typed "sec"
        matches nothing in the key space. Matching display names as well is
        what keeps mid-word typing working."""
        found = labels("908 n sec", region="pia")
        assert found, "a partially typed ordinal street should still suggest"
        assert "SECOND" in found[0].upper()

    def test_the_spelled_and_numeric_forms_both_work(self):
        assert labels("908 n second", region="pia")
        assert labels("908 n 2nd", region="pia")


class TestBehaviour:
    def test_a_street_only_query_suggests_streets(self):
        results = suggest("market")["suggestions"]
        assert results and all(r["kind"] == "street" for r in results)

    def test_a_numbered_query_suggests_addresses(self):
        results = suggest("1100 california")["suggestions"]
        assert results and all(r["kind"] == "address" for r in results)

    def test_every_suggestion_carries_coordinates_and_a_region(self):
        """The client uses these to skip a second geocode round trip."""
        for suggestion in suggest("1100 california")["suggestions"]:
            assert suggestion["lat"] and suggestion["lon"]
            assert suggestion["region"] in ("sf", "pia")

    def test_the_limit_is_respected_and_capped(self):
        assert len(suggest("a", limit=3)["suggestions"]) <= 3
        assert len(suggest("st", limit=10)["suggestions"]) <= 10

    def test_a_region_filter_restricts_results(self):
        for suggestion in suggest("main", region="pia")["suggestions"]:
            assert suggestion["region"] == "pia"


class TestShortAndHostileInput:
    """An autocomplete fires on every keystroke, including the first."""

    @pytest.mark.parametrize("query", ["", " ", "a", "x" * 200])
    def test_unusable_queries_return_empty_rather_than_an_error(self, query):
        """A 4xx mid-word would turn every keystroke into a UI error."""
        body = suggest(query)
        assert body["suggestions"] == []

    def test_a_query_matching_nothing_returns_empty(self):
        assert suggest("zzzzqqqxyz")["suggestions"] == []

    def test_punctuation_does_not_break_it(self):
        for query in ("1100 california st.", "o'farrell", "st. louis", "1100, california"):
            suggest(query)


class TestLatency:
    def test_suggestions_are_fast_enough_to_fire_per_keystroke(self):
        """The whole point of serving this ourselves is that it is free *and*
        fast. If it ever creeps past a few tens of milliseconds it stops being
        usable as you type and the design needs revisiting."""
        suggest("cal")  # warm the datasets

        worst = 0.0
        for query in ("m", "ma", "mar", "mark", "marke", "market", "1100 market"):
            started = time.perf_counter()
            suggest(query)
            worst = max(worst, (time.perf_counter() - started) * 1000.0)
        assert worst < 150, f"slowest keystroke took {worst:.0f}ms"
