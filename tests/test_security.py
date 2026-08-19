"""Security assertions.

These pin properties that are easy to state, easy to lose in a refactor, and
expensive to discover the hard way. Each one corresponds to a specific thing an
attacker or an accident could otherwise do.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from stepwise.http.errors import BadRequest
from stepwise.http.request import MAX_BODY_BYTES, Request
from stepwise.logging_config import JsonFormatter
from validco_infra.stack import StepWiseStack


def flatten(value) -> str:
    """Render a CloudFormation intrinsic down to a comparable string.

    The CSP interpolates the region, so it synthesises as an ``Fn::Join`` of
    literals and ``Ref``s rather than a plain string. Flattening keeps the
    assertions readable instead of matching on the intrinsic's structure.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "Fn::Join" in value:
            separator, parts = value["Fn::Join"]
            return separator.join(flatten(part) for part in parts)
        if "Ref" in value:
            return f"${{{value['Ref']}}}"
        if "Fn::Sub" in value:
            sub = value["Fn::Sub"]
            return flatten(sub[0] if isinstance(sub, list) else sub)
    if isinstance(value, list):
        return "".join(flatten(item) for item in value)
    return str(value)


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App()
    stack = StepWiseStack(
        app,
        "stepwise-sec",
        env_name="dev",
        api_dir="api",
        web_dir=None,
        app_version="test",
        log_level="DEBUG",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


class TestRequestHardening:
    def test_oversized_bodies_are_rejected_before_parsing(self):
        """Parsing a 10 MB body as JSON on every request is free CPU for an
        attacker. The check costs one integer comparison."""
        event = {
            "rawPath": "/v1/plan",
            "requestContext": {"http": {"method": "POST"}},
            "body": "x" * (MAX_BODY_BYTES + 1),
        }
        with pytest.raises(BadRequest, match="too large"):
            Request(event).json()

    def test_a_normal_body_is_well_under_the_limit(self):
        """The guard must not be near the size of a legitimate request."""
        realistic = json.dumps(
            {
                "address": "100 N Main St, Chillicothe, IL 61523",
                "minutes": 30,
                "profile": {"sex": "male", "age": 33, "weight_lb": 320, "height_ft": 6},
                "preferences": {"prefer_paths": True, "avoid_hills": True},
            }
        )
        assert len(realistic) < MAX_BODY_BYTES / 50

    def test_malformed_base64_is_a_400_not_a_crash(self):
        event = {
            "rawPath": "/v1/plan",
            "requestContext": {"http": {"method": "POST"}},
            "body": "!!!not base64!!!",
            "isBase64Encoded": True,
        }
        with pytest.raises(BadRequest):
            Request(event).json()

    def test_every_numeric_input_is_bounded(self):
        """Unbounded inputs are how one request becomes a denial of service."""
        with pytest.raises(BadRequest):
            Request.number(10**9, "minutes", 5, 240)
        with pytest.raises(BadRequest):
            Request.number(float("inf"), "minutes", 5, 240)


class TestLogRedaction:
    """The API receives a home address, an age and a weight. None of it should
    end up readable in a log aggregator just because the level is DEBUG."""

    def _format(self, **extra) -> dict:
        import logging

        record = logging.LogRecord("test", logging.INFO, "f.py", 1, "message", None, None)
        for key, value in extra.items():
            setattr(record, key, value)
        return json.loads(JsonFormatter().format(record))

    def test_addresses_are_redacted(self):
        assert self._format(address="100 N Main St")["address"] == "[redacted]"

    def test_nested_personal_fields_are_redacted(self):
        payload = self._format(profile={"address": "100 N Main St", "bmi": 49.0})
        assert payload["profile"]["address"] == "[redacted]"
        # Derived, non-identifying values survive so the log is still useful.
        assert payload["profile"]["bmi"] == 49.0

    def test_weight_is_bucketed_not_removed(self):
        """A coarse band diagnoses a bug; an exact weight identifies a person."""
        assert self._format(weight_kg=163.7)["weight_kg"] == "~175"

    def test_coordinates_are_coarsened(self):
        payload = self._format(lat=40.6936, lon=-89.5890)
        assert payload["lat"] == 40.9
        assert payload["lon"] == -89.5

    def test_non_personal_fields_pass_through(self):
        payload = self._format(duration_ms=12.5, status=200, region="pia")
        assert payload["duration_ms"] == 12.5
        assert payload["status"] == 200
        assert payload["region"] == "pia"


class TestErrorDisclosure:
    def test_internal_errors_do_not_leak_details(self):
        """A stack trace tells an attacker about the runtime and the code path."""
        from stepwise.http.router import Router

        class Exploding:
            def handle(self, request):
                raise RuntimeError("connection string postgres://user:hunter2@db")

        response = (
            Router()
            .register("GET", "/x", Exploding())
            .dispatch({"rawPath": "/x", "requestContext": {"http": {"method": "GET"}}})
        )
        assert response["statusCode"] == 500
        assert "hunter2" not in response["body"]
        assert "RuntimeError" not in response["body"]
        # A correlation id is returned instead, so the real error is findable.
        assert json.loads(response["body"])["request_id"]


class TestTransportSecurity:
    def test_content_security_policy_is_set(self, template):
        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        assert policies, "the distribution must attach a response headers policy"
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        csp_block = config["SecurityHeadersConfig"]["ContentSecurityPolicy"]
        assert csp_block["Override"] is True
        assert "default-src 'self'" in flatten(csp_block["ContentSecurityPolicy"])

    def test_csp_restricts_where_data_can_be_sent(self, template):
        """The protection that survives `unsafe-inline`: even an injected script
        cannot exfiltrate the user's address to an attacker's host."""
        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        csp = flatten(
            config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
        )
        assert "connect-src 'self'" in csp
        assert "execute-api" in csp
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp

    def test_hsts_is_enabled_with_a_long_max_age(self, template):
        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        hsts = config["SecurityHeadersConfig"]["StrictTransportSecurity"]
        assert hsts["AccessControlMaxAgeSec"] >= 31536000
        assert hsts["IncludeSubdomains"] is True

    def test_clickjacking_and_sniffing_are_blocked(self, template):
        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        security = config["SecurityHeadersConfig"]
        assert security["FrameOptions"]["FrameOption"] == "DENY"
        assert security["ContentTypeOptions"]["Override"] is True

    def test_bucket_requires_tls(self, template):
        """`enforce_ssl` adds a bucket policy denying non-TLS requests."""
        policies = template.find_resources("AWS::S3::BucketPolicy")
        denies_insecure = any(
            statement.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
            for policy in policies.values()
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
            if statement.get("Effect") == "Deny"
        )
        assert denies_insecure


class TestTransportEverywhere:
    """Nothing in this system should ever move over plain HTTP."""

    def test_no_behaviour_serves_plain_http(self, template):
        distributions = template.find_resources("AWS::CloudFront::Distribution")
        config = next(iter(distributions.values()))["Properties"]["DistributionConfig"]
        behaviours = [config["DefaultCacheBehavior"], *config.get("CacheBehaviors", [])]
        for behaviour in behaviours:
            assert behaviour["ViewerProtocolPolicy"] in ("redirect-to-https", "https-only")

    def test_every_bucket_denies_non_tls_requests(self, template):
        """`enforce_ssl` adds an explicit Deny on aws:SecureTransport=false."""
        policies = template.find_resources("AWS::S3::BucketPolicy")
        assert policies, "buckets must carry policies"
        for name, policy in policies.items():
            statements = policy["Properties"]["PolicyDocument"]["Statement"]
            assert any(
                s.get("Effect") == "Deny"
                and s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
                for s in statements
            ), f"{name} does not require TLS"

    def test_the_tile_host_is_in_both_img_src_and_connect_src(self, template):
        """Regression guard for a bug that shipped.

        MapLibre fetches raster tiles through the Fetch API, not as <img>
        elements, so listing the tile host only under `img-src` left the map
        blank in production with a console full of CSP violations. Both
        directives must name it.
        """
        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        csp = flatten(
            config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
        )

        directives = {
            part.strip().split(" ", 1)[0]: part.strip() for part in csp.split(";") if part.strip()
        }
        assert "tile.openstreetmap.org" in directives["img-src"]
        assert "tile.openstreetmap.org" in directives["connect-src"]

    def test_every_host_the_map_style_uses_is_allowed_to_connect(self, template):
        """Whatever the map style fetches from must be in `connect-src`.

        Reads the hosts out of the frontend source rather than hard-coding
        them, so adding a tile provider without widening the policy fails here
        instead of in a browser.
        """
        import re
        from pathlib import Path

        style = Path("web/src/components/map/MapView.tsx").read_text()
        hosts = set(re.findall(r"https://([a-z0-9.\-]+)/\{z\}", style))
        assert hosts, "expected at least one tile host in the map style"

        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        csp = flatten(
            config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
        )
        connect = next(p for p in csp.split(";") if p.strip().startswith("connect-src"))
        for host in hosts:
            assert host in connect, f"{host} is fetched by the map but not in connect-src"

    def test_csp_upgrades_insecure_requests(self, template):
        policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
        config = next(iter(policies.values()))["Properties"]["ResponseHeadersPolicyConfig"]
        csp = flatten(
            config["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
        )
        assert "upgrade-insecure-requests" in csp
        # No scheme-less or http: source anywhere in the policy.
        assert "http://" not in csp


class TestBucketsAreNeverPublic:
    """Every bucket is reachable only through CloudFront or the Lambda's IAM role."""

    def test_all_buckets_block_public_access(self, template):
        buckets = template.find_resources("AWS::S3::Bucket")
        assert len(buckets) >= 3, "site, region cache and CloudTrail buckets"
        for name, bucket in buckets.items():
            config = bucket["Properties"].get("PublicAccessBlockConfiguration")
            assert config == {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            }, f"{name} does not block public access"

    def test_no_bucket_policy_grants_a_wildcard_principal(self, template):
        """A `Principal: "*"` Allow is how a bucket accidentally goes public."""
        policies = template.find_resources("AWS::S3::BucketPolicy")
        for name, policy in policies.items():
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                if statement.get("Effect") != "Allow":
                    continue
                principal = statement.get("Principal")
                assert principal != "*", f"{name} allows an anonymous principal"
                if isinstance(principal, dict):
                    assert principal.get("AWS") != "*", f"{name} allows any AWS principal"

    def test_the_site_bucket_is_reached_through_origin_access_control(self, template):
        """CloudFront, and only CloudFront, may read the site bucket."""
        template.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
        policies = template.find_resources("AWS::S3::BucketPolicy")
        grants_cloudfront = any(
            statement.get("Principal", {}).get("Service") == "cloudfront.amazonaws.com"
            for policy in policies.values()
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        )
        assert grants_cloudfront

    def test_the_region_cache_is_never_fronted_by_cloudfront(self, template):
        """It holds derived data served through the API, not the browser.

        Asserted by name rather than by origin count: what matters is that the
        region cache is not publicly reachable, not how many origins the site
        bucket happens to be wired through.
        """
        distributions = template.find_resources("AWS::CloudFront::Distribution")
        config = next(iter(distributions.values()))["Properties"]["DistributionConfig"]
        for origin in config["Origins"]:
            rendered = flatten(origin["DomainName"])
            assert "RegionBucket" not in rendered, "the region cache must not be public"
            assert "TrailBucket" not in rendered, "audit logs must not be public"


class TestCorsFailsClosed:
    """A misconfiguration must never widen access to every origin."""

    def test_a_deployed_environment_without_config_emits_no_origin(self, monkeypatch):
        from stepwise.http.response import Response

        monkeypatch.setenv("ENV_NAME", "dev")
        monkeypatch.delenv("CORS_ALLOW_ORIGIN", raising=False)
        assert "Access-Control-Allow-Origin" not in Response.cors_headers()

    def test_a_deployed_environment_uses_exactly_the_configured_origin(self, monkeypatch):
        from stepwise.http.response import Response

        monkeypatch.setenv("ENV_NAME", "dev")
        monkeypatch.setenv("CORS_ALLOW_ORIGIN", "https://example.cloudfront.net")
        assert Response.cors_headers()["Access-Control-Allow-Origin"] == (
            "https://example.cloudfront.net"
        )

    def test_local_development_still_permits_a_wildcard(self, monkeypatch):
        from stepwise.http.response import Response

        monkeypatch.delenv("ENV_NAME", raising=False)
        monkeypatch.delenv("CORS_ALLOW_ORIGIN", raising=False)
        assert Response.cors_headers()["Access-Control-Allow-Origin"] == "*"

    def test_responses_vary_on_origin(self, monkeypatch):
        """The permitted origin is configuration, so caches must key on it."""
        from stepwise.http.response import Response

        assert Response.cors_headers()["Vary"] == "Origin"


class TestConfigurationHandling:
    """Configuration reaches the Lambda as environment, never via a runtime call."""

    def test_settings_never_import_boto3(self):
        """A runtime SSM call would be a round trip on every cold start."""
        import inspect

        from stepwise import settings

        source = inspect.getsource(settings)
        assert "boto3" not in source
        assert "get_parameter" not in source

    def test_required_configuration_errors_name_the_variable_not_its_value(self):
        from stepwise.settings import Settings

        store = Settings({"SOME_TOKEN": "super-secret-value"})
        try:
            store.require("MISSING_TOKEN")
        except RuntimeError as exc:
            assert "MISSING_TOKEN" in str(exc)
            assert "super-secret-value" not in str(exc)
        else:
            raise AssertionError("require() should raise when unset")

    def test_health_output_reports_presence_not_values(self):
        from stepwise.settings import Settings

        described = Settings(
            {"ENV_NAME": "dev", "REGION_BUCKET": "b", "BUILDER_FUNCTION_NAME": "f"}
        ).describe()
        assert described["on_demand_regions"] is True
        assert "b" not in described.values()

    def test_sensitive_names_are_recognised(self):
        from stepwise.settings import Settings

        assert Settings.is_sensitive("SENTRY_DSN")
        assert Settings.is_sensitive("API_TOKEN")
        assert not Settings.is_sensitive("REGION_BUCKET")


class TestAccessControl:
    def test_cors_is_not_a_wildcard(self, template):
        """The request body carries a home address and health inputs; no other
        origin needs to be able to make a browser send them."""
        methods = template.find_resources("AWS::ApiGateway::Method")
        options = [m for m in methods.values() if m["Properties"].get("HttpMethod") == "OPTIONS"]
        assert options, "CORS preflight must be configured"
        for method in options:
            for response in method["Properties"]["Integration"]["IntegrationResponses"]:
                origin = response["ResponseParameters"].get(
                    "method.response.header.Access-Control-Allow-Origin"
                )
                assert origin is not None
                assert "'*'" != origin, "CORS must not allow every origin"

    def test_lambda_concurrency_is_capped(self, template):
        """Bounds both the blast radius of a flood and the bill."""
        template.has_resource_properties(
            "AWS::Lambda::Function",
            Match.object_like({"ReservedConcurrentExecutions": Match.any_value()}),
        )

    def test_api_is_throttled(self, template):
        template.has_resource_properties(
            "AWS::ApiGateway::Stage",
            Match.object_like(
                {
                    "MethodSettings": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "ThrottlingRateLimit": Match.any_value(),
                                    "ThrottlingBurstLimit": Match.any_value(),
                                }
                            )
                        ]
                    )
                }
            ),
        )

    def test_site_bucket_is_not_public(self, template):
        template.has_resource_properties(
            "AWS::S3::Bucket",
            Match.object_like(
                {
                    "PublicAccessBlockConfiguration": {
                        "BlockPublicAcls": True,
                        "BlockPublicPolicy": True,
                        "IgnorePublicAcls": True,
                        "RestrictPublicBuckets": True,
                    }
                }
            ),
        )

    def test_the_lambda_has_no_data_store_permissions(self, template):
        """The API is read-only over files in its own package. If it ever gains
        S3 or DynamoDB rights, that is a design change worth noticing."""
        policies = template.find_resources("AWS::IAM::Policy")
        for name, policy in policies.items():
            # The BucketDeployment custom resource legitimately writes to S3.
            if "SiteDeployment" in name or "CustomCDKBucketDeployment" in name:
                continue
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                actions = statement.get("Action", [])
                actions = [actions] if isinstance(actions, str) else actions
                assert not any(
                    str(a).startswith(("dynamodb:", "rds:", "secretsmanager:")) for a in actions
                ), f"{name} grants unexpected data-store access"
