"""Assertions about the synthesised CloudFormation template.

Infrastructure requirements are easy to state and easy to lose in a refactor.
These tests pin the ones that were asked for explicitly — X-Ray, CloudTrail,
DEBUG logging, a private bucket — so removing any of them fails CI rather than
quietly shipping.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template
from validco_infra.stack import StepWiseStack


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
