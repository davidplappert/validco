"""Tests for address parsing and normalisation.

The normaliser is shared with the offline builder, which keys the geocoding
index by its output. If these two ever disagree the index becomes unlookupable
in a way no other test would catch, so the round-trip cases below are load
bearing.
"""

from __future__ import annotations

import pytest
from stepwise.services.geocoder import AddressParser, StreetNormalizer

_NORMALIZER = StreetNormalizer()
_PARSER = AddressParser()


def normalize_street(name):
    """Adapter keeping these tests readable."""
    return _NORMALIZER.normalize(name)


def parse_address(text):
    """Adapter keeping these tests readable."""
    return _PARSER.parse(text)


def parse_latlon(text):
    """Adapter keeping these tests readable."""
    coord = _PARSER.parse_latlon(text)
    return (coord.lat, coord.lon) if coord else None


class TestNormalizeStreet:
    @pytest.mark.parametrize(
        "written,canonical",
        [
            ("N Main St", "north main street"),
            ("North Main Street", "north main street"),
            ("n. main st.", "north main street"),
            ("Market St", "market street"),
            ("MARKET STREET", "market street"),
            ("W Sycamore St", "west sycamore street"),
            ("Van Ness Ave", "van ness avenue"),
            ("Kearny Blvd", "kearny boulevard"),
            ("SE Riverside Pkwy", "southeast riverside parkway"),
        ],
    )
    def test_abbreviations_fold_to_one_key(self, written, canonical):
        assert normalize_street(written) == canonical

    @pytest.mark.parametrize(
        "written,canonical",
        [
            ("N 2nd St", "north 2nd street"),
            ("N Second St", "north 2nd street"),
            ("N 02nd St", "north 2nd street"),
            ("03RD ST", "3rd street"),
            ("Third Street", "3rd street"),
            ("12th Ave", "12th avenue"),
            ("Twelfth Avenue", "12th avenue"),
        ],
    )
    def test_numbered_streets_fold_to_one_key(self, written, canonical):
        """Overture is not self-consistent about numbered streets.

        Peoria spells them out ("North SECOND Street"), San Francisco zero-pads
        the digits ("03RD ST"), and users type neither. All three have to reach
        the same key or a search for a city hall on 2nd Street simply fails —
        which it did, until this was added.
        """
        assert normalize_street(written) == canonical

    def test_variants_agree(self):
        """The whole point: what Overture stores and what a user types must meet."""
        assert normalize_street("100 N Main St".split(" ", 1)[1]) == normalize_street(
            "North MAIN Street"
        )

    def test_punctuation_and_case_are_irrelevant(self):
        assert normalize_street("O'Farrell St.") == normalize_street("OFarrell Street")


class TestParseAddress:
    def test_full_us_address(self):
        p = parse_address("100 N Main St, Morton, IL 61550")
        assert p.number == 100
        assert p.street_norm == "north main street"
        assert p.postcode == "61550"
        assert p.state == "IL"

    def test_directional_prefix_survives_the_house_number(self):
        """Regression: a greedy suffix match once ate the "N" from "708 N
        Main Street" and searched for "Main Street", which does not exist."""
        assert parse_address("100 N Main St").street_norm == "north main street"
        assert parse_address("100 S Main St").street_norm == "south main street"
        assert parse_address("55 E Oak Ave").street_norm == "east oak avenue"

    def test_adjacent_letter_is_a_unit_suffix(self):
        p = parse_address("450A Market St")
        assert p.number == 450
        assert p.street_norm == "market street"

    def test_street_only(self):
        p = parse_address("Market Street")
        assert p.number is None
        assert p.street_norm == "market street"

    def test_trailing_number_form(self):
        assert parse_address("Main St 100").number == 100

    def test_locality_does_not_pollute_the_street(self):
        p = parse_address("1000 California St, San Francisco, CA 94108")
        assert p.street_norm == "california street"
        assert p.postcode == "94108"

    def test_empty_input_is_not_an_error(self):
        p = parse_address("")
        assert p.number is None and p.street_norm == ""


class TestParseLatLon:
    def test_accepts_a_coordinate_pair(self):
        assert parse_latlon("37.7749, -122.4194") == (37.7749, -122.4194)
        assert parse_latlon("40.6103 -89.4616") == (40.6103, -89.4616)

    def test_rejects_out_of_range_and_prose(self):
        assert parse_latlon("999, -122") is None
        assert parse_latlon("100 N Main St") is None
        assert parse_latlon("") is None
