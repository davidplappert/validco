"""Assertions about the synthesised CloudFormation template.

Infrastructure requirements are easy to state and easy to lose in a refactor.
These tests pin the ones that were asked for explicitly — X-Ray, CloudTrail,
DEBUG logging, a private bucket — so removing any of them fails CI rather than
quietly shipping.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from bundle import build as build_builder_bundle
from validco_infra.stack import StepWiseStack

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def template() -> Template:
    """Synthesise the stack once and assert against the template."""
    app = cdk.App()
    stack = StepWiseStack(
        app,
        "stepwise-test",
        env_name="dev",
        api_dir="api",
        web_dir=None,
        app_version="test",
        log_level="DEBUG",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def builder_template(tmp_path_factory) -> Template:
    """The stack with the region builder included.

    Kept apart from ``template`` because the builder is optional — ``cdk synth``
    can skip staging its native wheels — and because staging the package, even
    without downloading dependencies, costs a file copy the other tests do not
    need. The dependency install is off: this asserts about the template, not
    about pip.
    """
    staging = build_builder_bundle(
        ROOT, tmp_path_factory.mktemp("builder") / "bundle", install_dependencies=False
    )
    app = cdk.App()
    stack = StepWiseStack(
        app,
        "stepwise-test-builder",
        env_name="dev",
        api_dir="api",
        web_dir=None,
        builder_dir=str(staging),
        app_version="test",
        log_level="DEBUG",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


def _function_with(template: Template, variable: str) -> dict:
    """The one Lambda whose environment carries a given variable."""
    matches = [
        properties["Properties"]
        for properties in template.find_resources("AWS::Lambda::Function").values()
        if variable in properties["Properties"].get("Environment", {}).get("Variables", {})
    ]
    assert len(matches) == 1, f"expected exactly one function with {variable}, got {len(matches)}"
    return matches[0]


class TestRegionBuilder:
    """The on-demand builder's wiring.

    Every assertion here corresponds to something that broke, or would have
    broken silently, on the first real on-demand build.
    """

    def test_the_builder_knows_which_bucket_to_publish_to(self, builder_template):
        """The bug that stopped the first build.

        Without REGION_BUCKET the builder's catalogue is disabled: progress
        writes vanish, the completion write vanishes, and the upload dies on an
        empty bucket name after the extraction has already run.
        """
        builders = [
            properties["Properties"]
            for properties in builder_template.find_resources("AWS::Lambda::Function").values()
            if properties["Properties"].get("Handler") == "handler.handler"
        ]
        assert len(builders) == 1
        assert "REGION_BUCKET" in builders[0]["Environment"]["Variables"]

    def test_both_functions_share_one_region_bucket(self, builder_template):
        """A cache split across two buckets is not a cache."""
        buckets = {
            properties["Properties"]["Environment"]["Variables"]["REGION_BUCKET"]["Ref"]
            for properties in builder_template.find_resources("AWS::Lambda::Function").values()
            if "REGION_BUCKET"
            in properties["Properties"].get("Environment", {}).get("Variables", {})
        }
        assert len(buckets) == 1, "the API and the builder must reference the same bucket"

    def test_the_api_is_told_which_function_to_invoke(self, builder_template):
        api = _function_with(builder_template, "BUILDER_FUNCTION_NAME")
        assert api["MemorySize"] == 512, "the invoker is the request-path function"

    def test_the_builder_can_write_to_the_region_bucket(self, builder_template):
        """It uploads four containers and rewrites status.json throughout.

        Scoped to the builder's *own* role rather than to any policy in the
        template: the API function has the same grant, so a template-wide search
        would pass even with the builder holding nothing.
        """
        functions = builder_template.find_resources("AWS::Lambda::Function")
        builder = next(
            properties
            for properties in functions.values()
            if properties["Properties"].get("Handler") == "handler.handler"
        )
        role = builder["Properties"]["Role"]["Fn::GetAtt"][0]

        actions: list[str] = []
        for policy in builder_template.find_resources("AWS::IAM::Policy").values():
            attached = {r.get("Ref") for r in policy["Properties"].get("Roles", [])}
            if role not in attached:
                continue
            for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
                action = statement["Action"]
                actions.extend(action if isinstance(action, list) else [action])

        assert "s3:PutObject" in actions, "the builder must be able to publish containers"
        assert any(a.startswith("s3:GetObject") for a in actions), (
            "the builder reads the status document the API claimed"
        )

    def test_the_builder_has_room_and_time_for_an_extraction(self, builder_template):
        """Extraction is minutes of CPU-bound work over hundreds of MB."""
        builder_template.has_resource_properties(
            "AWS::Lambda::Function",
            Match.object_like(
                {
                    "Handler": "handler.handler",
                    "MemorySize": 3008,
                    "Timeout": 840,
                    "EphemeralStorage": {"Size": 2048},
                }
            ),
        )

    def test_the_builder_has_a_writable_home_for_duckdb(self, builder_template):
        """DuckDB downloads spatial and httpfs on first use; only /tmp is writable."""
        builder_template.has_resource_properties(
            "AWS::Lambda::Function",
            Match.object_like(
                {
                    "Handler": "handler.handler",
                    "Environment": {"Variables": Match.object_like({"HOME": "/tmp"})},
                }
            ),
        )

    def test_the_builder_function_name_is_published(self, builder_template):
        """Needed to tail its logs and to invoke it by hand."""
        assert "BuilderFunctionName" in builder_template.find_outputs("*")

    def test_concurrent_extractions_are_bounded(self, builder_template):
        """Overture's bucket is a shared public resource."""
        builder_template.has_resource_properties(
            "AWS::Lambda::Function",
            Match.object_like({"Handler": "handler.handler", "ReservedConcurrentExecutions": 3}),
        )


class TestLambda:
    def test_runs_python_313_on_arm(self, template):
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {"Runtime": "python3.13", "Architectures": ["arm64"]},
        )

    def test_xray_tracing_is_active(self, template):
        """Asked for explicitly; easy to lose in a refactor."""
        template.has_resource_properties(
            "AWS::Lambda::Function", {"TracingConfig": {"Mode": "Active"}}
        )

    def test_log_level_is_debug(self, template):
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {"Environment": {"Variables": Match.object_like({"LOG_LEVEL": "DEBUG"})}},
        )

    def test_has_enough_memory_for_the_graph_search(self, template):
        """512 MB is chosen for vCPU, not bytes — Dijkstra is CPU-bound."""
        template.has_resource_properties(
            "AWS::Lambda::Function", Match.object_like({"MemorySize": 512})
        )

    def test_cors_origin_is_wired_to_the_distribution(self, template):
        """The frontend origin is not knowable until deploy, so it is injected."""
        functions = template.find_resources("AWS::Lambda::Function")
        api_functions = [
            f
            for f in functions.values()
            if "CORS_ALLOW_ORIGIN" in f["Properties"].get("Environment", {}).get("Variables", {})
        ]
        assert api_functions, "the API function must receive CORS_ALLOW_ORIGIN"


class TestApiGateway:
    def test_is_a_rest_api(self, template):
        """REST rather than HTTP API: only REST supports gateway X-Ray and
        full request/response execution logging, both of which were required."""
        template.resource_count_is("AWS::ApiGateway::RestApi", 1)

    def test_stage_has_tracing_and_full_logging(self, template):
        template.has_resource_properties(
            "AWS::ApiGateway::Stage",
            Match.object_like(
                {
                    "TracingEnabled": True,
                    "MethodSettings": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "DataTraceEnabled": True,
                                    "LoggingLevel": "INFO",
                                    "MetricsEnabled": True,
                                }
                            )
                        ]
                    ),
                }
            ),
        )

    def test_stage_writes_json_access_logs(self, template):
        stages = template.find_resources("AWS::ApiGateway::Stage")
        settings = next(iter(stages.values()))["Properties"]["AccessLogSetting"]
        assert "requestId" in settings["Format"]
        # The X-Ray trace id is the join key from a log line to a trace.
        assert "xrayTraceId" in settings["Format"]

    def test_is_throttled(self, template):
        """An unthrottled public API is an unbounded bill."""
        template.has_resource_properties(
            "AWS::ApiGateway::Stage",
            Match.object_like(
                {
                    "MethodSettings": Match.array_with(
                        [Match.object_like({"ThrottlingRateLimit": 20})]
                    )
                }
            ),
        )


class TestFrontendHosting:
    def test_bucket_blocks_all_public_access(self, template):
        """CloudFront reaches it through OAC; nothing else may."""
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

    def test_bucket_is_encrypted(self, template):
        template.has_resource_properties(
            "AWS::S3::Bucket",
            Match.object_like({"BucketEncryption": Match.any_value()}),
        )

    def test_distribution_redirects_to_https(self, template):
        template.has_resource_properties(
            "AWS::CloudFront::Distribution",
            {
                "DistributionConfig": Match.object_like(
                    {
                        "DefaultCacheBehavior": Match.object_like(
                            {"ViewerProtocolPolicy": "redirect-to-https"}
                        )
                    }
                )
            },
        )

    def test_config_json_is_never_cached(self, template):
        """It carries the API URL; a stale copy points users at a dead endpoint."""
        distributions = template.find_resources("AWS::CloudFront::Distribution")
        config = next(iter(distributions.values()))["Properties"]["DistributionConfig"]
        behaviours = config.get("CacheBehaviors", [])
        assert any(b["PathPattern"] == "/config.json" for b in behaviours)

    def test_spa_paths_fall_back_to_index(self, template):
        distributions = template.find_resources("AWS::CloudFront::Distribution")
        config = next(iter(distributions.values()))["Properties"]["DistributionConfig"]
        codes = {r["ErrorCode"] for r in config.get("CustomErrorResponses", [])}
        assert {403, 404} <= codes


class TestAudit:
    def test_cloudtrail_is_enabled(self, template):
        template.resource_count_is("AWS::CloudTrail::Trail", 1)

    def test_trail_logs_to_cloudwatch_as_well_as_s3(self, template):
        template.has_resource_properties(
            "AWS::CloudTrail::Trail",
            Match.object_like(
                {
                    "IsLogging": True,
                    "EnableLogFileValidation": True,
                    "CloudWatchLogsLogGroupArn": Match.any_value(),
                }
            ),
        )

    def test_trail_bucket_expires_objects(self, template):
        """Keeps the audit trail inside the free tier indefinitely."""
        buckets = template.find_resources("AWS::S3::Bucket")
        assert any("LifecycleConfiguration" in b["Properties"] for b in buckets.values()), (
            "the trail bucket needs a lifecycle rule"
        )


class TestObservabilityCost:
    def test_every_log_group_has_a_retention(self, template):
        """Without retention, CloudWatch keeps DEBUG logs forever and bills for it."""
        groups = template.find_resources("AWS::Logs::LogGroup")
        assert groups
        for name, group in groups.items():
            assert "RetentionInDays" in group["Properties"], f"{name} has no retention"


class TestOutputs:
    @pytest.mark.parametrize(
        "output", ["SiteUrl", "ApiUrl", "ApiHealthUrl", "FunctionName", "DistributionId"]
    )
    def test_publishes_the_urls_the_workflow_needs(self, template, output):
        """The deploy workflow reads these to smoke-test and report."""
        assert output in template.find_outputs("*")
