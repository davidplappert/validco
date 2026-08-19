"""Tests for the HTTP edge: request parsing, responses, errors and routing."""

from __future__ import annotations

import base64
import json

import pytest
from stepwise.http.errors import ApiError, BadRequest, NotFound, Unprocessable
from stepwise.http.request import Request
from stepwise.http.response import Response
from stepwise.http.router import Router


def v1_event(method="GET", path="/v1/plan", stage=None, body=None, query=None):
    """An API Gateway REST (payload format 1.0) proxy event."""
    return {
        "httpMethod": method,
        "path": path,
        "requestContext": {"stage": stage, "identity": {"sourceIp": "1.2.3.4"}},
        "queryStringParameters": query,
        "body": body,
    }


def v2_event(method="GET", path="/v1/plan", body=None, query=None):
    """An API Gateway HTTP (payload format 2.0) event."""
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path, "sourceIp": "5.6.7.8"}},
        "queryStringParameters": query,
        "body": body,
    }


class TestRequestNormalisation:
    def test_parses_payload_format_1(self):
        request = Request(v1_event("POST", "/v1/plan"))
        assert request.route_key == ("POST", "/v1/plan")
        assert request.identity["ip"] == "1.2.3.4"

    def test_parses_payload_format_2(self):
        request = Request(v2_event("POST", "/v1/plan"))
        assert request.route_key == ("POST", "/v1/plan")
        assert request.identity["ip"] == "5.6.7.8"

    def test_strips_the_deployment_stage_prefix(self):
        """A REST API deployed to a named stage prefixes every path with it."""
        request = Request(v1_event("GET", "/dev/v1/health", stage="dev"))
        assert request.path == "/v1/health"

    def test_keeps_a_path_that_merely_starts_with_the_stage_name(self):
        """`/devices` must not lose four characters because the stage is `dev`."""
        request = Request(v1_event("GET", "/devices", stage="dev"))
        assert request.path == "/devices"

    def test_ignores_the_default_stage(self):
        request = Request(v1_event("GET", "/v1/health", stage="$default"))
        assert request.path == "/v1/health"

    def test_strips_a_trailing_slash(self):
        assert Request(v2_event("GET", "/v1/health/")).path == "/v1/health"

    def test_root_survives_normalisation(self):
        assert Request(v2_event("GET", "/")).path == "/"

    def test_uppercases_the_method(self):
        assert Request(v2_event("post", "/v1/plan")).method == "POST"


class TestRequestBody:
    def test_parses_json(self):
        request = Request(v2_event("POST", "/v1/plan", body=json.dumps({"minutes": 30})))
        assert request.json() == {"minutes": 30}

    def test_caches_the_parsed_body(self):
        request = Request(v2_event("POST", "/v1/plan", body='{"a": 1}'))
        assert request.json() is request.json()

    def test_decodes_base64_bodies(self):
        encoded = base64.b64encode(b'{"minutes": 45}').decode()
        event = v2_event("POST", "/v1/plan", body=encoded)
        event["isBase64Encoded"] = True
        assert Request(event).json() == {"minutes": 45}

    def test_missing_body_is_an_empty_object(self):
        assert Request(v2_event("POST", "/v1/plan")).json() == {}

    def test_malformed_json_is_a_bad_request(self):
        with pytest.raises(BadRequest, match="not valid JSON"):
            Request(v2_event("POST", "/v1/plan", body="{nope")).json()

    def test_a_json_array_body_is_rejected(self):
        with pytest.raises(BadRequest, match="must be a JSON object"):
            Request(v2_event("POST", "/v1/plan", body="[1,2,3]")).json()


class TestValidationHelpers:
    def test_accepts_a_value_in_range(self):
        assert Request.number(30, "minutes", 5, 240) == 30.0

    def test_accepts_a_numeric_string(self):
        assert Request.number("30", "minutes", 5, 240) == 30.0

    def test_applies_the_default_when_absent(self):
        assert Request.number(None, "minutes", 5, 240, 30.0) == 30.0
        assert Request.number("", "minutes", 5, 240, 30.0) == 30.0

    def test_requires_a_value_with_no_default(self):
        with pytest.raises(BadRequest, match="minutes is required"):
            Request.number(None, "minutes", 5, 240)

    def test_rejects_out_of_range(self):
        with pytest.raises(BadRequest, match="between 5 and 240"):
            Request.number(9999, "minutes", 5, 240)

    def test_rejects_non_numeric(self):
        with pytest.raises(BadRequest, match="must be a number"):
            Request.number("soon", "minutes", 5, 240)

    def test_integer_truncates(self):
        assert Request.integer("4", "max_routes", 1, 6) == 4

    def test_required_query_rejects_blank(self):
        request = Request(v2_event("GET", "/v1/geocode", query={"q": "   "}))
        with pytest.raises(BadRequest, match="q is required"):
            request.required_query("q")


class TestErrors:
    def test_status_codes(self):
        assert BadRequest("x").status == 400
        assert NotFound("x").status == 404
        assert Unprocessable("x").status == 422
        assert ApiError("x").status == 500

    def test_detail_is_carried_into_the_body(self):
        error = NotFound("no such street", suggestions=["Market Street"])
        assert error.to_dict() == {
            "error": "no such street",
            "suggestions": ["Market Street"],
        }


class TestResponse:
    def test_renders_a_lambda_proxy_response(self):
        rendered = Response.ok({"ok": True}).to_lambda()
        assert rendered["statusCode"] == 200
        assert json.loads(rendered["body"]) == {"ok": True}
        assert rendered["headers"]["content-type"] == "application/json"

    def test_includes_cors_headers(self):
        headers = Response.ok({}).to_lambda()["headers"]
        assert "Access-Control-Allow-Origin" in headers
        assert headers["Access-Control-Allow-Methods"] == "GET,POST,OPTIONS"

    def test_honours_the_configured_origin(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGIN", "https://example.cloudfront.net")
        headers = Response.ok({}).to_lambda()["headers"]
        assert headers["Access-Control-Allow-Origin"] == "https://example.cloudfront.net"

    def test_no_content(self):
        assert Response.no_content().to_lambda()["statusCode"] == 204


class TestRouter:
    class Echo:
        """A controller that reports which route ran."""

        def __init__(self, name):
            self.name = name
            self.calls = 0

        def handle(self, request):
            self.calls += 1
            return Response.ok({"route": self.name})

    class Exploding:
        """A controller that always fails, to exercise the 500 path."""

        def handle(self, request):
            raise RuntimeError("boom")

    class Rejecting:
        """A controller that raises a typed API error."""

        def handle(self, request):
            raise Unprocessable("outside coverage", regions=["sf"])

    def test_dispatches_to_the_registered_controller(self):
        echo = self.Echo("health")
        router = Router().register("GET", "/v1/health", echo)
        response = router.dispatch(v2_event("GET", "/v1/health"))
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["route"] == "health"
        assert echo.calls == 1

    def test_unknown_route_lists_what_exists(self):
        router = Router().register("GET", "/v1/health", self.Echo("health"))
        response = router.dispatch(v2_event("GET", "/v1/nope"))
        assert response["statusCode"] == 404
        assert "GET /v1/health" in json.loads(response["body"])["available"]

    def test_method_is_part_of_the_route_key(self):
        router = Router().register("POST", "/v1/plan", self.Echo("plan"))
        assert router.dispatch(v2_event("GET", "/v1/plan"))["statusCode"] == 404

    def test_options_short_circuits_without_a_controller(self):
        router = Router()
        assert router.dispatch(v2_event("OPTIONS", "/v1/plan"))["statusCode"] == 204

    def test_typed_errors_become_their_status(self):
        router = Router().register("POST", "/v1/plan", self.Rejecting())
        response = router.dispatch(v2_event("POST", "/v1/plan"))
        body = json.loads(response["body"])
        assert response["statusCode"] == 422
        assert body["regions"] == ["sf"]
        assert "request_id" in body

    def test_unexpected_exceptions_become_a_500_with_a_request_id(self):
        """The caller gets a correlation key, not a stack trace."""
        router = Router().register("GET", "/v1/boom", self.Exploding())
        response = router.dispatch(v2_event("GET", "/v1/boom"))
        body = json.loads(response["body"])
        assert response["statusCode"] == 500
        assert body["error"] == "internal error"
        assert body["request_id"]
        assert "boom" not in response["body"]

    def test_cold_start_flag_flips_after_the_first_request(self):
        router = Router().register("GET", "/v1/health", self.Echo("health"))
        assert router.cold_start is True
        router.dispatch(v2_event("GET", "/v1/health"))
        assert router.cold_start is False

    def test_registration_chains(self):
        router = (
            Router().register("GET", "/a", self.Echo("a")).register("GET", "/b", self.Echo("b"))
        )
        assert router.routes() == ["GET /a", "GET /b"]
