"""The StepWise dev stack.

One stack, one environment, everything free-tier. The shape is:

    CloudFront ──> S3 (private, OAC)         the static Next.js export
    Browser    ──> API Gateway REST ──> Lambda   the routing/health API

Two AWS-issued domains, no Route 53, no certificates to manage.

Free-tier notes (the constraint this was built under)
-----------------------------------------------------
* **Lambda** — 1M requests and 400k GB-seconds a month are *always* free. At
  512 MB this app gets ~800k seconds of compute a month for nothing.
* **CloudFront** — 1 TB egress and 10M requests a month, always free.
* **S3** — a few MB of site assets. Storage past the 12-month 5 GB tier costs
  fractions of a cent.
* **API Gateway REST** — 1M calls/month for the first 12 months. This is the
  one component with a time-limited tier; HTTP APIs are cheaper per call but
  do **not** support X-Ray tracing or full request/response execution logging
  at the gateway, both of which were explicitly wanted here.
* **X-Ray** — 100k traces recorded a month, always free.
* **CloudTrail** — one trail of management events per account is free; the
  bucket has a 30-day lifecycle rule so the storage stays negligible.
* **CloudWatch Logs** — 5 GB ingest a month free. Log retention is capped at
  two weeks so nothing accumulates.

Observability is deliberately turned up well past what a production service of
this size would run: DEBUG on the Lambda, full request/response tracing at the
gateway, and X-Ray on both. The README says how to dial it back.
"""

from __future__ import annotations

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Size,
    Stack,
)
from aws_cdk import (
    aws_apigateway as apigw,
)
from aws_cdk import (
    aws_cloudfront as cloudfront,
)
from aws_cdk import (
    aws_cloudfront_origins as origins,
)
from aws_cdk import (
    aws_cloudtrail as cloudtrail,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_s3_deployment as s3deploy,
)
from constructs import Construct

# Two weeks is plenty to debug a demo and keeps CloudWatch ingest well inside
# the free tier even with DEBUG logging on every request.
LOG_RETENTION = logs.RetentionDays.TWO_WEEKS


class StepWiseStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        api_dir: str,
        web_dir: str | None,
        app_version: str,
        log_level: str = "DEBUG",
        trace_request_bodies: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.env_name = env_name
        # Full request/response logging at the gateway was asked for explicitly.
        # It is a knob rather than a constant because those bodies contain the
        # user's address, age and weight: switch it off and the app-level logs
        # (which redact those fields) become the only record.
        self.trace_request_bodies = trace_request_bodies

        site_bucket = self._site_bucket()
        distribution = self._distribution(site_bucket)
        fn = self._function(api_dir, app_version, log_level, distribution)
        api = self._rest_api(fn, distribution)

        # The frontend is a static export, so it cannot be built with the API
        # URL baked in — the URL does not exist until this stack deploys. It
        # fetches /config.json at runtime instead, and that file is generated
        # here from the real values.
        self._deploy_site(site_bucket, distribution, web_dir, api, app_version)
        self._trail()

        CfnOutput(
            self,
            "SiteUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="Public web app URL",
        )
        CfnOutput(self, "ApiUrl", value=api.url.rstrip("/"), description="Public API base URL")
        CfnOutput(
            self,
            "ApiHealthUrl",
            value=f"{api.url.rstrip('/')}/v1/health",
            description="Liveness probe",
        )
        CfnOutput(self, "SiteBucketName", value=site_bucket.bucket_name)
        CfnOutput(self, "FunctionName", value=fn.function_name)
        CfnOutput(self, "DistributionId", value=distribution.distribution_id)

    # --- storage + CDN ----------------------------------------------------

    def _site_bucket(self) -> s3.Bucket:
        return s3.Bucket(
            self,
            "SiteBucket",
            # The bucket is never public; CloudFront reaches it through Origin
            # Access Control, so the only way in is via the distribution.
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

    def _security_headers(self) -> cloudfront.ResponseHeadersPolicy:
        """Security headers for every response CloudFront serves.

        The Content-Security-Policy is the substantive one. Note the honest
        limitation: a Next.js **static export** emits inline RSC payload scripts
        and there is no server to mint a per-request nonce, so ``script-src``
        has to allow ``'unsafe-inline'``. Hash-pinning them would work but the
        hashes change on every build.

        The protections that still matter a great deal are intact:

        * ``connect-src`` restricts where the page may send data — so even a
          successful script injection cannot exfiltrate the user's address and
          health inputs to an attacker-controlled host.
        * ``frame-ancestors 'none'`` prevents clickjacking.
        * ``object-src 'none'`` and ``base-uri 'self'`` close two classic
          injection vectors.

        ``connect-src`` uses a wildcard for the API Gateway host rather than the
        exact URL because the distribution is created before the API, and
        referencing ``api.url`` here would make CloudFormation cycle through
        distribution -> api -> function -> distribution.
        """
        csp = "; ".join(
            [
                "default-src 'self'",
                # See the docstring: unavoidable for a nonce-less static export.
                "script-src 'self' 'unsafe-inline'",
                # React sets style attributes (progress bar widths).
                "style-src 'self' 'unsafe-inline'",
                # OSM raster tiles, plus the data:/blob: URIs MapLibre generates.
                "img-src 'self' data: blob: https://tile.openstreetmap.org",
                # MapLibre runs its tile workers from a blob URL.
                "worker-src 'self' blob:",
                f"connect-src 'self' https://*.execute-api.{Aws.REGION}.amazonaws.com",
                "font-src 'self' data:",
                "object-src 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
                "upgrade-insecure-requests",
            ]
        )

        return cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            comment=f"stepwise-{self.env_name} security headers",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=csp, override=True
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    preload=True,
                    override=True,
                ),
            ),
        )

    def _distribution(self, bucket: s3.Bucket) -> cloudfront.Distribution:
        headers = self._security_headers()
        return cloudfront.Distribution(
            self,
            "SiteDistribution",
            comment=f"stepwise-{self.env_name} static site",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers,
                compress=True,
            ),
            additional_behaviors={
                # config.json carries the API URL. If CloudFront cached it for a
                # day, a redeploy that moved the API would leave every visitor
                # pointed at the old one until the cache expired.
                "/config.json": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    response_headers_policy=headers,
                    compress=True,
                ),
            },
            error_responses=[
                # A static export has no server-side router, so unknown paths
                # are handed to index.html rather than showing S3's XML error.
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
            ],
            # PRICE_CLASS_100 (North America + Europe) is the cheapest tier and
            # covers where this will realistically be opened.
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            enable_logging=False,  # access logs would need another S3 bucket
        )

    # --- compute ----------------------------------------------------------

    def _function(
        self,
        api_dir: str,
        app_version: str,
        log_level: str,
        distribution: cloudfront.Distribution,
    ) -> lambda_.Function:
        log_group = logs.LogGroup(
            self,
            "ApiFunctionLogs",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

        return lambda_.Function(
            self,
            "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            # Graviton is ~20% cheaper per GB-second and the code is pure
            # Python, so there is no native wheel to worry about.
            architecture=lambda_.Architecture.ARM_64,
            handler="stepwise.handler.handler",
            code=lambda_.Code.from_asset(api_dir),
            # 512 MB is chosen for CPU, not memory: Lambda scales vCPU with
            # memory, and Dijkstra is CPU-bound. The graph itself is ~25 MB.
            memory_size=512,
            timeout=Duration.seconds(20),
            # Caps blast radius in both directions: a traffic spike or a
            # deliberate flood cannot scale this to the account limit, and it
            # cannot run up a bill. API Gateway throttling is the first line;
            # this is the backstop if that is ever raised.
            reserved_concurrent_executions=25,
            tracing=lambda_.Tracing.ACTIVE,
            log_group=log_group,
            environment={
                "LOG_LEVEL": log_level,
                "APP_VERSION": app_version,
                "ENV_NAME": self.env_name,
                "CORS_ALLOW_ORIGIN": f"https://{distribution.distribution_domain_name}",
                # Surfaces X-Ray SDK issues as logs instead of exceptions if the
                # SDK is ever added; harmless otherwise.
                "AWS_XRAY_CONTEXT_MISSING": "LOG_ERROR",
                "PYTHONUNBUFFERED": "1",
            },
        )

    # --- API --------------------------------------------------------------

    def _rest_api(
        self, fn: lambda_.Function, distribution: cloudfront.Distribution
    ) -> apigw.LambdaRestApi:
        access_logs = logs.LogGroup(
            self,
            "ApiAccessLogs",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

        api = apigw.LambdaRestApi(
            self,
            "Api",
            handler=fn,
            proxy=True,
            rest_api_name=f"stepwise-{self.env_name}",
            description="StepWise walking-health API over Overture Maps data",
            deploy_options=apigw.StageOptions(
                stage_name=self.env_name,
                tracing_enabled=True,
                # INFO + data trace logs the full request and response bodies to
                # CloudWatch. This is the "as debug as possible" setting and is
                # only appropriate because the API carries no credentials and no
                # persisted user data.
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=self.trace_request_bodies,
                metrics_enabled=True,
                access_log_destination=apigw.LogGroupLogDestination(access_logs),
                access_log_format=apigw.AccessLogFormat.custom(_access_log_format()),
                throttling_rate_limit=20,
                throttling_burst_limit=40,
            ),
            # Preflight is answered by API Gateway itself, so an OPTIONS request
            # never spends a Lambda invocation.
            default_cors_preflight_options=apigw.CorsOptions(
                # Only this deployment's own frontend, not "*". The API holds no
                # credentials so a wildcard would not be exploitable, but the
                # request body carries the user's home address and health
                # inputs, and there is no reason for another origin to be able
                # to make the browser send them.
                allow_origins=[f"https://{distribution.distribution_domain_name}"],
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type"],
                max_age=Duration.days(1),
            ),
            # JSON is already small and CloudFront is not in front of the API,
            # so let API Gateway compress responses itself.
            min_compression_size=Size.kibibytes(1),
        )

        # API Gateway needs an account-level role to write to CloudWatch Logs at
        # all. CDK creates it automatically for the first REST API in an
        # account; making it explicit avoids a confusing "no logs at all"
        # failure mode on a fresh account like this one.
        api.node.add_dependency(self._api_account_role())
        return api

    def _api_account_role(self) -> apigw.CfnAccount:
        role = iam.Role(
            self,
            "ApiGatewayCloudWatchRole",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonAPIGatewayPushToCloudWatchLogs"
                )
            ],
        )
        return apigw.CfnAccount(self, "ApiGatewayAccount", cloud_watch_role_arn=role.role_arn)

    # --- site contents ----------------------------------------------------

    def _deploy_site(
        self,
        bucket: s3.Bucket,
        distribution: cloudfront.Distribution,
        web_dir: str | None,
        api: apigw.LambdaRestApi,
        app_version: str,
    ) -> None:
        sources = [
            s3deploy.Source.json_data(
                "config.json",
                {
                    "apiBaseUrl": api.url.rstrip("/"),
                    "env": self.env_name,
                    "version": app_version,
                    "region": Aws.REGION,
                },
            )
        ]
        if web_dir:
            sources.insert(0, s3deploy.Source.asset(web_dir))

        s3deploy.BucketDeployment(
            self,
            "SiteDeployment",
            sources=sources,
            destination_bucket=bucket,
            distribution=distribution,
            distribution_paths=["/*"],
            prune=True,
            memory_limit=512,
        )

    # --- audit ------------------------------------------------------------

    def _trail(self) -> cloudtrail.Trail:
        """A management-event trail for the account.

        One trail of management events per account is free. Data events (S3
        object reads, Lambda invokes) are deliberately *not* enabled: they are
        billed per event and would be the one thing in this stack that costs
        real money under load.
        """
        trail_bucket = s3.Bucket(
            self,
            "TrailBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30), enabled=True)],
        )

        trail_logs = logs.LogGroup(
            self,
            "TrailLogs",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

        return cloudtrail.Trail(
            self,
            "Trail",
            bucket=trail_bucket,
            cloud_watch_log_group=trail_logs,
            send_to_cloud_watch_logs=True,
            include_global_service_events=True,
            is_multi_region_trail=False,
            enable_file_validation=True,
            management_events=cloudtrail.ReadWriteType.ALL,
        )


def _access_log_format() -> str:
    """Access log fields, as JSON, for CloudWatch Logs Insights.

    ``requestId`` is the join key to the Lambda's own structured logs, and
    ``xrayTraceId`` is the join key to the trace — so one request can be
    followed from the edge, through the gateway, into the function.
    """
    import json

    return json.dumps(
        {
            "requestId": "$context.requestId",
            "xrayTraceId": "$context.xrayTraceId",
            "ip": "$context.identity.sourceIp",
            "userAgent": "$context.identity.userAgent",
            "requestTime": "$context.requestTime",
            "httpMethod": "$context.httpMethod",
            "path": "$context.path",
            "status": "$context.status",
            "protocol": "$context.protocol",
            "responseLength": "$context.responseLength",
            "responseLatency": "$context.responseLatency",
            "integrationLatency": "$context.integrationLatency",
            "integrationStatus": "$context.integrationStatus",
            "error": "$context.error.message",
        }
    )
