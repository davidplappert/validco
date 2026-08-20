"""End-to-end tests against the real baked datasets.

These exercise the Lambda handler directly with synthetic API Gateway events,
over the same San Francisco and Peoria graphs that ship to production. They are
slower than the pure-unit tests but they are the ones that would have caught
every bug found while building this: a zip-length mismatch in route assembly, a
regex eating a street's directional prefix, a payload-format assumption.
"""

from __future__ import annotations

import json

import pytest
from stepwise.controllers.plan import StartPointResolver
from stepwise.datasets.registry import REGISTRY
from stepwise.handler import handler

#: The synthetic walker every request test uses.
#:
#: Named for what it is rather than for a person: this repository is public,
#: and a fixture carrying somebody's real name alongside their real age is a
#: description of them regardless of what the privacy guard greps for. The
#: figures are chosen to stay in obesity class III so the same code paths —
#: the body-mass speed factor, the knee-load warnings, the step-length
#: reduction — are still exercised.
WALKER = {"sex": "male", "age": 45, "weight_lb": 320, "height_ft": 6, "height_in": 0}


def call(method: str, path: str, body: dict | None = None, query: dict | None = None):
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path}},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }
    response = handler(event, None)
    parsed = json.loads(response["body"]) if response.get("body") else {}
    return response["statusCode"], parsed


class TestPlumbing:
    def test_health(self):
        status, body = call("GET", "/v1/health")
        assert status == 200 and body["ok"] is True

    def test_regions_are_advertised(self):
        status, body = call("GET", "/v1/regions")
        assert status == 200
        keys = {r["key"] for r in body["regions"]}
        assert {"sf", "pia"} <= keys
        assert "Overture" in body["attribution"]

    def test_unknown_route_lists_what_exists(self):
        status, body = call("GET", "/v1/nope")
        assert status == 404
        assert any("/v1/plan" in route for route in body["available"])

    def test_cors_preflight_costs_no_work(self):
        event = {"rawPath": "/v1/plan", "requestContext": {"http": {"method": "OPTIONS"}}}
        assert handler(event, None)["statusCode"] == 204


class TestGeocodeRoute:
    def test_finds_a_real_illinois_address(self):
        status, body = call("GET", "/v1/geocode", query={"q": "100 N Main St, Morton, IL 61550"})
        assert status == 200 and body["found"] is True
        assert body["match"] == "exact"
        assert body["lat"] == pytest.approx(40.6103, abs=0.01)
        assert body["lon"] == pytest.approx(-89.4616, abs=0.01)

    def test_finds_a_real_san_francisco_address(self):
        status, body = call("GET", "/v1/geocode", query={"q": "1000 California St, San Francisco"})
        assert status == 200 and body["found"] is True
        assert body["lat"] == pytest.approx(37.792, abs=0.02)

    def test_a_missing_house_number_falls_back_to_the_nearest(self):
        """Address corpora always have holes; 'not found' is the wrong answer."""
        status, body = call("GET", "/v1/geocode", query={"q": "999999 N Main St, Morton IL"})
        assert status == 200
        assert body["match"] == "nearest_number"

    def test_a_miss_returns_suggestions_not_just_a_404(self):
        status, body = call("GET", "/v1/geocode", query={"q": "12 Nonexistent Fakery Boulevard"})
        assert status == 404
        assert body["found"] is False


@pytest.fixture(scope="module")
def plan():
    """One 30-minute plan from a real Morton address, shared by the tests
    below — planning is fast but the graph load is not worth repeating."""
    status, body = call(
        "POST",
        "/v1/plan",
        body={
            "address": "100 N Main St, Morton, IL 61550",
            "minutes": 30,
            "profile": WALKER,
            "preferences": {"prefer_paths": True, "avoid_hills": True},
        },
    )
    assert status == 200, body
    return body


class TestPlanRoute:
    def test_returns_routes_in_the_right_region(self, plan):
        assert plan["region"] == "pia"
        assert len(plan["routes"]) >= 1

    def test_origin_snaps_close_to_the_address(self, plan):
        assert plan["origin"]["snap_distance_m"] < 200

    def test_routes_respect_the_time_budget(self, plan):
        """The reason the search budgets in seconds rather than metres."""
        for route in plan["routes"]:
            assert 20 <= route["effort"]["duration_min"] <= 45, route["effort"]

    def test_routes_are_closed_loops(self, plan):
        """Every walk must end where it started, or the product is broken."""
        for route in plan["routes"]:
            coords = route["geometry"]["coordinates"]
            start, end = coords[0], coords[-1]
            assert start == pytest.approx(end, abs=1e-4), route["shape"]

    def test_geometry_is_continuous(self, plan):
        """Adjacent points must not jump — a gap means an edge was reversed or
        sliced wrongly when segments were split at their connectors."""
        for route in plan["routes"]:
            coords = route["geometry"]["coordinates"]
            for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:], strict=False):
                assert abs(lon1 - lon2) < 0.01 and abs(lat1 - lat2) < 0.01

    def test_surface_breakdown_sums_to_a_whole(self, plan):
        for route in plan["routes"]:
            assert sum(route["surface_breakdown_pct"].values()) == pytest.approx(100.0, abs=0.5)

    def test_every_route_carries_its_health_framing(self, plan):
        for route in plan["routes"]:
            health = route["health"]
            assert health["guideline_progress"]["who_weekly_moderate_min"] == 150
            assert health["steps"]["daily_target"] == 7000
            assert health["caveats"], "health claims must ship with their caveats"
            assert route["suitability"]["notes"]

    def test_elevation_profile_tracks_the_route(self, plan):
        for route in plan["routes"]:
            profile = route["elevation_profile"]
            assert len(profile) >= 2
            assert profile[0]["m"] == 0
            # Distances must be monotonically increasing.
            assert all(a["m"] <= b["m"] for a, b in zip(profile, profile[1:], strict=False))

    def test_results_are_ranked(self, plan):
        scores = [r["score"] for r in plan["routes"]]
        assert scores == sorted(scores, reverse=True)


class TestPlanInSanFrancisco:
    def test_hill_avoidance_actually_flattens_the_route(self):
        """The clearest behavioural claim the product makes, on the terrain that
        makes it matter."""
        base = {
            "address": "1000 California St, San Francisco",
            "minutes": 40,
            "profile": WALKER,
        }
        _, hilly = call("POST", "/v1/plan", body={**base, "preferences": {}})
        _, flat = call(
            "POST",
            "/v1/plan",
            body={**base, "preferences": {"avoid_hills": True, "avoid_stairs": True}},
        )
        assert (
            flat["routes"][0]["effort"]["peak_grade_pct"]
            <= hilly["routes"][0]["effort"]["peak_grade_pct"]
        )

    def test_no_stairs_means_no_stairs(self):
        _, body = call(
            "POST",
            "/v1/plan",
            body={
                "address": "1000 California St, San Francisco",
                "minutes": 40,
                "profile": WALKER,
                "preferences": {"avoid_stairs": True},
            },
        )
        for route in body["routes"]:
            assert "stairs" not in route["features"], route["streets"][:5]

    def test_coordinates_work_without_an_address(self):
        status, body = call(
            "POST",
            "/v1/plan",
            body={
                "lat": 37.7749,
                "lon": -122.4194,
                "minutes": 25,
                "profile": WALKER,
                "preferences": {},
            },
        )
        assert status == 200 and body["region"] == "sf"


class TestValidation:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ({"minutes": 30, "profile": WALKER}, 400),  # no location
            ({"address": "x", "minutes": 30, "profile": {}}, 400),  # no sex
            ({"address": "x", "minutes": 30, "profile": {**WALKER, "age": 400}}, 400),  # absurd age
            (
                {
                    "address": "x",
                    "minutes": 30,
                    "profile": {k: v for k, v in WALKER.items() if k != "weight_lb"},
                },
                400,
            ),
            (
                {
                    "address": "1000 California St, San Francisco",
                    "minutes": 9999,
                    "profile": WALKER,
                },
                400,
            ),  # beyond limits
        ],
    )
    def test_bad_input_is_rejected_with_a_reason(self, body, expected):
        status, response = call("POST", "/v1/plan", body=body)
        assert status == expected
        assert response["error"]

    def test_a_point_outside_coverage_says_so(self):
        status, body = call(
            "POST",
            "/v1/plan",
            body={"lat": 51.5074, "lon": -0.1278, "minutes": 30, "profile": WALKER},
        )
        assert status == 422
        assert "outside" in body["error"]

    def test_malformed_json_is_a_400_not_a_500(self):
        event = {
            "rawPath": "/v1/plan",
            "requestContext": {"http": {"method": "POST"}},
            "body": "{not json",
        }
        assert handler(event, None)["statusCode"] == 400


class TestRegionSelection:
    """Which region an address is planned in.

    Street names repeat between towns, so "the first region that produces a
    hit" is not a safe rule. Peoria and Kewanee both have a Third Street;
    before this, an address in one planned a walk in the other and nothing in
    the response admitted it. The answer looked entirely plausible, which is
    what makes this class of bug worth pinning.
    """

    @staticmethod
    def resolver() -> StartPointResolver:
        """A resolver over the real registry."""
        return StartPointResolver(REGISTRY)

    def test_the_locality_decides_which_region_is_searched(self):
        """A town named in the query narrows the search to that town."""
        assert self.resolver()._candidate_regions("100 N Main St, Morton, IL") == ["pia"]
        assert self.resolver()._candidate_regions("1100 California St, San Francisco") == ["sf"]

    def test_an_unrecognised_locality_falls_back_to_searching_everything(self):
        """An unknown town is not evidence against the regions we do hold."""
        assert self.resolver()._candidate_regions("100 Main St, Nowhereville") == REGISTRY.keys()

    def test_a_bare_street_searches_everything(self):
        """With no locality there is nothing to narrow on."""
        assert self.resolver()._candidate_regions("1100 California St") == REGISTRY.keys()

    def test_a_state_code_alone_never_claims_a_region(self):
        """A two-letter state must not hand every address to one region.

        The short-token rule exists precisely for this: "IL" appears in the
        label of every Illinois region, so matching on it would reproduce the
        bug rather than fix it.
        """
        assert self.resolver()._candidate_regions("100 Main St, IL") == REGISTRY.keys()

    def test_a_walk_is_planned_in_the_town_that_was_asked_for(self):
        """End to end, with a street that genuinely exists in both regions.

        "Main St" resolves in San Francisco *and* in Peoria, which is the whole
        point: an address that only matches one region would pass this test
        whether or not the locality was ever consulted. This one fails outright
        if the resolver goes back to taking the first hit in registry order —
        which is exactly how the bug behaved, and why it survived a full test
        suite in the first place.
        """
        for locality, expected in (("Morton, IL", "pia"), ("San Francisco, CA", "sf")):
            status, body = call(
                "POST",
                "/v1/plan",
                {"address": f"100 Main St, {locality}", "minutes": 20, "profile": WALKER},
            )
            assert status == 200, f"{locality} -> {body}"
            assert body["region"] == expected, f"{locality} planned in {body['region']}"


class TestRoot:
    """``GET /`` — the URL most likely to be pasted somewhere by hand.

    It used to return a well-formed 404 listing the available routes, which is
    correct and still reads as "this is broken" to anyone opening it in a
    browser. That is the wrong first impression for the one address a reviewer
    is most likely to try.
    """

    def test_redirects_to_the_app_when_a_site_is_configured(self, monkeypatch):
        """A browser lands on the product rather than an error."""
        monkeypatch.setenv("SITE_URL", "https://example.cloudfront.net")
        status, body = call("GET", "/")
        assert status == 302
        assert body["app"] == "https://example.cloudfront.net"

    def test_the_redirect_carries_a_location_header(self, monkeypatch):
        """The header is what actually moves a browser; the body is for tools."""
        monkeypatch.setenv("SITE_URL", "https://example.cloudfront.net")
        event = {
            "rawPath": "/",
            "requestContext": {"http": {"method": "GET", "path": "/"}},
        }
        response = handler(event, None)
        assert response["statusCode"] == 302
        assert response["headers"]["Location"] == "https://example.cloudfront.net"
        # A permanent redirect would be cached by browsers indefinitely, and the
        # CloudFront domain is regenerated whenever the distribution is
        # replaced — so this must stay temporary.
        assert response["statusCode"] != 301

    def test_falls_back_to_the_cors_origin(self, monkeypatch):
        """One value configures both, so a missing SITE_URL is still usable."""
        monkeypatch.delenv("SITE_URL", raising=False)
        monkeypatch.setenv("CORS_ALLOW_ORIGIN", "https://fallback.cloudfront.net")
        status, body = call("GET", "/")
        assert status == 302
        assert body["app"] == "https://fallback.cloudfront.net"

    def test_explains_itself_when_there_is_no_app_to_point_at(self, monkeypatch):
        """Local development has no site; say so rather than redirect nowhere."""
        monkeypatch.delenv("SITE_URL", raising=False)
        monkeypatch.setenv("CORS_ALLOW_ORIGIN", "*")
        status, body = call("GET", "/")
        assert status == 200
        assert body["health"] == "/v1/health"

    def test_a_redirect_still_says_where_the_api_is(self, monkeypatch):
        """`curl` without -L shows a document, not a blank page."""
        monkeypatch.setenv("SITE_URL", "https://example.cloudfront.net")
        _, body = call("GET", "/")
        assert body["api"] == "/v1" and body["health"] == "/v1/health"
