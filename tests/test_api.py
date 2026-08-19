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
from stepwise.handler import handler

DAVID = {"sex": "male", "age": 33, "weight_lb": 320, "height_ft": 6, "height_in": 0}


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
    def test_finds_a_real_chillicothe_address(self):
        status, body = call(
            "GET", "/v1/geocode", query={"q": "100 N Main St, Chillicothe, IL 61523"}
        )
        assert status == 200 and body["found"] is True
        assert body["match"] == "exact"
        assert body["lat"] == pytest.approx(40.6936, abs=0.01)
        assert body["lon"] == pytest.approx(-89.5890, abs=0.01)

    def test_finds_a_real_san_francisco_address(self):
        status, body = call("GET", "/v1/geocode", query={"q": "1000 California St, San Francisco"})
        assert status == 200 and body["found"] is True
        assert body["lat"] == pytest.approx(37.792, abs=0.02)

    def test_a_missing_house_number_falls_back_to_the_nearest(self):
        """Address corpora always have holes; 'not found' is the wrong answer."""
        status, body = call(
            "GET", "/v1/geocode", query={"q": "709999 N Main St, Chillicothe IL"}
        )
        assert status == 200
        assert body["match"] == "nearest_number"

    def test_a_miss_returns_suggestions_not_just_a_404(self):
        status, body = call("GET", "/v1/geocode", query={"q": "12 Nonexistent Fakery Boulevard"})
        assert status == 404
        assert body["found"] is False


@pytest.fixture(scope="module")
def plan():
    """One 30-minute plan from a real Chillicothe address, shared by the tests
    below — planning is fast but the graph load is not worth repeating."""
    status, body = call(
        "POST",
        "/v1/plan",
        body={
            "address": "100 N Main St, Chillicothe, IL 61523",
            "minutes": 30,
            "profile": DAVID,
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
            "profile": DAVID,
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
                "profile": DAVID,
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
                "profile": DAVID,
                "preferences": {},
            },
        )
        assert status == 200 and body["region"] == "sf"


class TestValidation:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ({"minutes": 30, "profile": DAVID}, 400),  # no location
            ({"address": "x", "minutes": 30, "profile": {}}, 400),  # no sex
            ({"address": "x", "minutes": 30, "profile": {**DAVID, "age": 400}}, 400),  # absurd age
            (
                {
                    "address": "x",
                    "minutes": 30,
                    "profile": {k: v for k, v in DAVID.items() if k != "weight_lb"},
                },
                400,
            ),
            (
                {"address": "1000 California St, San Francisco", "minutes": 9999, "profile": DAVID},
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
            body={"lat": 51.5074, "lon": -0.1278, "minutes": 30, "profile": DAVID},
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
