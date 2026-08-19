"""Tests for the builder's Overture division lookup.

This module had no coverage at all, and it is the first thing an on-demand
build touches: get it wrong and the extraction runs perfectly against the wrong
part of the planet. Two of the tests below pin failures that did exactly that —
"Paris, France" resolving to Frances, South Australia, and "Kyoto, Japan"
matching a locality genuinely named Japan in East Java.

The parser tests need no network. The resolver tests drive a fake connection so
the SQL and the order the candidates are tried in can be asserted without
scanning Overture.
"""

from __future__ import annotations

import pytest
from place import PlaceParser, PlaceResolver

RELEASE = "s3://overturemaps-us-west-2/release/test"


class TestPlaceParser:
    @pytest.mark.parametrize(
        ("query", "name", "state", "country"),
        [
            ("Austin, TX", "Austin", "TX", "US"),
            ("Boulder, Colorado", "Boulder", "CO", "US"),
            ("Springfield", "Springfield", None, None),
            ("Austin, Texas, USA", "Austin", "TX", "US"),
            ("Galesburg, IL 61401", "Galesburg", "IL", "US"),
            ("San Francisco, CA, United States", "San Francisco", "CA", "US"),
        ],
    )
    def test_splits_a_us_place(self, query, name, state, country):
        parsed = PlaceParser().parse(query)
        assert (parsed.name, parsed.state, parsed.country) == (name, state, country)

    @pytest.mark.parametrize(
        ("query", "name", "country"),
        [
            ("Paris, France", "Paris", "FR"),
            ("Kyoto, Japan", "Kyoto", "JP"),
            ("Zurich, Switzerland", "Zurich", "CH"),
            ("Oxford, UK", "Oxford", "GB"),
            ("Edinburgh, Scotland", "Edinburgh", "GB"),
            ("Lisbon, PT", "Lisbon", "PT"),
        ],
    )
    def test_recognises_a_trailing_country(self, query, name, country):
        """Without this, "Kyoto, Japan" matches a real locality called Japan."""
        parsed = PlaceParser().parse(query)
        assert parsed.name == name
        assert parsed.country == country
        assert parsed.state is None

    @pytest.mark.parametrize("query", ["Springfield, IL", "Bloomington, IN"])
    def test_a_state_beats_the_country_code_it_collides_with(self, query):
        """IL is Illinois and Israel; IN is Indiana and India. States win."""
        parsed = PlaceParser().parse(query)
        assert parsed.country == "US"
        assert parsed.state is not None

    def test_drops_a_house_number_from_the_locality(self):
        """Otherwise "100 N Main St" searches divisions for a street."""
        parsed = PlaceParser().parse("100 N Main St, Morton, IL 61550")
        assert parsed.name == "Morton"
        assert "N Main St" in parsed.candidates

    def test_an_address_keeps_the_city_as_the_first_candidate(self):
        parsed = PlaceParser().parse("1600 Amphitheatre Pkwy, Mountain View, CA")
        assert parsed.candidates[0] == "Mountain View"
        assert parsed.state == "CA"

    def test_an_unrecognised_qualifier_stays_a_candidate(self):
        """The country table cannot know everything, so nothing is discarded."""
        parsed = PlaceParser().parse("Paris, Ruritania")
        assert parsed.candidates == ["Ruritania", "Paris"]

    def test_candidates_are_deduplicated(self):
        assert PlaceParser().parse("Austin, Austin").candidates == ["Austin"]

    @pytest.mark.parametrize("query", ["", "   ", None])
    def test_empty_input_yields_no_candidates(self, query):
        parsed = PlaceParser().parse(query)
        assert parsed.candidates == []
        assert parsed.name == ""

    def test_a_postcode_alone_is_not_a_locality(self):
        assert PlaceParser().parse("61401").candidates == []

    def test_repr_names_what_it_found(self):
        """It goes into the build logs, so it has to be readable."""
        text = repr(PlaceParser().parse("Austin, TX"))
        assert "Austin" in text and "TX" in text


class FakeConnection:
    """A DuckDB stand-in that replays canned rows and records the queries."""

    def __init__(self, *results: list):
        """Queue one result set per ``execute`` call, in order."""
        self.results = list(results)
        self.calls: list[tuple[str, list]] = []

    def execute(self, sql: str, params: list | None = None):
        """Record the call and hand back the next queued result set."""
        self.calls.append((sql, list(params or [])))
        return self

    def fetchall(self) -> list:
        """Pop the queued rows for the query just executed."""
        return self.results.pop(0) if self.results else []


def row(name: str, subtype: str = "locality", country: str = "US", region: str = "US-IL") -> tuple:
    """One divisions row in the shape ``_describe`` expects."""
    return (name, subtype, country, region, -90.5, 40.9, -90.3, 41.0)


class TestPlaceResolver:
    def test_returns_the_first_exact_match(self):
        connection = FakeConnection([row("Galesburg")])
        found = PlaceResolver(connection, RELEASE).resolve_name("Galesburg, IL")
        assert found["name"] == "Galesburg"
        assert found["label"] == "Galesburg, IL"
        assert found["bbox"] == [-90.5, 40.9, -90.3, 41.0]

    def test_a_state_narrows_the_query(self):
        connection = FakeConnection([row("Galesburg")])
        PlaceResolver(connection, RELEASE).resolve_name("Galesburg, IL")
        _, params = connection.calls[0]
        assert "US-IL" in params

    def test_folds_accents_in_the_same_query(self):
        """Overture stores "Zürich"; nobody types the diaeresis."""
        connection = FakeConnection([row("Zürich", country="CH", region="CH-ZH")])
        found = PlaceResolver(connection, RELEASE).resolve_name("Zurich, Switzerland")
        sql, _ = connection.calls[0]
        assert "strip_accents" in sql
        assert found["label"] == "Zürich, ZH"

    def test_tries_every_candidate_before_giving_up_on_exactness(self):
        """Regression: "Paris, Ruritania" must not stop at the unknown tail."""
        connection = FakeConnection([], [row("Paris", country="FR", region="FR-IDF")])
        found = PlaceResolver(connection, RELEASE).resolve_name("Paris, Ruritania")
        assert [params[0] for _, params in connection.calls] == ["Ruritania", "Paris"]
        assert found["name"] == "Paris"

    def test_prefix_search_is_the_last_resort_not_the_second(self):
        """A fuzzy hit on the first candidate must never beat an exact later one.

        This is the Frances-of-South-Australia bug: the old resolver tried one
        name, missed, and went straight to a prefix match that happily returned
        a village on another continent.
        """
        connection = FakeConnection([], [row("Paris", country="FR", region="FR-IDF")])
        PlaceResolver(connection, RELEASE).resolve_name("Paris, Ruritania")
        assert all("LIKE" not in sql for sql, _ in connection.calls)

    def test_falls_back_to_a_prefix_when_nothing_matches_exactly(self):
        connection = FakeConnection([], [row("Galesburg")])
        found = PlaceResolver(connection, RELEASE).resolve_name("Galesbur")
        assert "LIKE" in connection.calls[-1][0]
        assert found["name"] == "Galesburg"

    def test_returns_none_when_the_place_does_not_exist(self):
        connection = FakeConnection([], [])
        assert PlaceResolver(connection, RELEASE).resolve_name("Nowhereville") is None

    def test_an_empty_query_never_touches_the_database(self):
        connection = FakeConnection()
        assert PlaceResolver(connection, RELEASE).resolve_name("") is None
        assert connection.calls == []

    def test_resolves_a_coordinate_by_containment(self):
        connection = FakeConnection([row("Galesburg")])
        found = PlaceResolver(connection, RELEASE).resolve_point(40.95, -90.37)
        sql, params = connection.calls[0]
        assert params == [-90.37, -90.37, 40.95, 40.95]
        # Smallest containing division wins, so a town beats the county.
        assert "ASC" in sql
        assert found["label"] == "Galesburg, IL"

    def test_a_coordinate_in_the_ocean_resolves_to_nothing(self):
        connection = FakeConnection([])
        assert PlaceResolver(connection, RELEASE).resolve_point(0.0, -140.0) is None

    def test_labels_fall_back_to_the_country_without_a_region(self):
        connection = FakeConnection([row("Singapore", country="SG", region=None)])
        found = PlaceResolver(connection, RELEASE).resolve_name("Singapore")
        assert found["label"] == "Singapore, SG"
