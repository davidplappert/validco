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

import logging

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

LOG = logging.getLogger(__name__)

# Two weeks is plenty to debug a demo and keeps CloudWatch ingest well inside
# the free tier even with DEBUG logging on every request.
LOG_RETENTION = logs.RetentionDays.TWO_WEEKS

#: The basemap tile host. Declared once because it has to appear in two CSP
#: directives — see the note in `_security_headers` — and the two must agree.
TILE_HOST = "https://tile.openstreetmap.org"


class StepWiseStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        api_dir: str,
        web_dir: str | None,
        builder_dir: str | None = None,
        app_version: str,
        log_level: str = "DEBUG",
        trace_request_bodies: bool = True,
        parameters: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.env_name = env_name
        # Full request/response logging at the gateway was asked for explicitly.
        # It is a knob rather than a constant because those bodies contain the
        # user's address, age and weight: switch it off and the app-level logs
        # (which redact those fields) become the only record.
        self.trace_request_bodies = trace_request_bodies

        # Configuration read from SSM at *deploy* time by the workflow and
        # handed in here, rather than fetched by the Lambda at run time. See
        # `stepwise.settings` for why: a runtime GetParameter is a round trip on
        # every cold start, and reading os.environ cannot fail.
        #
        # Everything stored in SSM is a SecureString, so it is KMS-encrypted at
        # rest. Note that a value copied into a Lambda environment variable is
        # then readable via GetFunctionConfiguration — fine for configuration,
        # wrong for a high-value secret. Nothing here is one.
        self.parameters = parameters or {}
        if self.parameters:
            LOG.info(
                "injecting %d parameter(s) from SSM into the function environment",
                len(self.parameters),
            )

        site_bucket = self._site_bucket()
        distribution = self._distribution(site_bucket)

        # The on-demand coverage store. Regions are extracted once, cached
        # here, and served to every container thereafter.
        region_bucket = self._region_bucket()
        builder = (
            self._builder_function(builder_dir, log_level, region_bucket) if builder_dir else None
        )

        fn = self._function(api_dir, app_version, log_level, distribution, region_bucket, builder)
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
        CfnOutput(
            self,
            "RegionBucketName",
            value=region_bucket.bucket_name,
            description="Cache of on-demand region datasets",
        )
        if builder is not None:
            CfnOutput(
                self,
                "BuilderFunctionName",
                value=builder.function_name,
                description="Lambda that extracts a new region from Overture",
            )
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
                f"img-src 'self' data: blob: {TILE_HOST}",
                # MapLibre runs its tile workers from a blob URL.
                "worker-src 'self' blob:",
                # The tile host belongs here as well as in img-src. MapLibre
                # fetches raster tiles through the Fetch API rather than as
                # <img> elements — so it needs the tile host in connect-src, and
                # img-src alone leaves the map blank with a console full of CSP
                # violations. Found the hard way on the first deployment.
                f"connect-src 'self' {TILE_HOST} https://*.execute-api.{Aws.REGION}.amazonaws.com",
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

    def _region_bucket(self) -> s3.Bucket:
        """Where on-demand regions are cached once built.

        No lifecycle expiry: this is a shared cache whose whole value is that a
        city extracted for one person is instant for the next. A region is
        roughly 10-25 MB, so even a hundred cities is well inside the free tier,
        and evicting them would only force repeat extractions.
        """
        return s3.Bucket(
            self,
            "RegionBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                # Half-finished multipart uploads from a builder that was
                # killed mid-upload would otherwise accrue storage silently.
                s3.LifecycleRule(abort_incomplete_multipart_upload_after=Duration.days(1))
            ],
        )

    def _builder_function(
        self, builder_dir: str, log_level: str, region_bucket: s3.Bucket
    ) -> lambda_.Function:
        """The region extractor.

        A separate function from the API on purpose. It carries DuckDB and
        Pillow — about 80 MB — needs minutes rather than milliseconds, and wants
        far more memory. Sharing a package with the request path would inflict
        all of that on every plan request's cold start.

        It needs the region bucket for the same reason the API does: it writes
        progress into ``status.json`` throughout the build and uploads the baked
        containers at the end. The bucket is passed in rather than created here
        so the two functions share one cache.
        """
        log_group = logs.LogGroup(
            self,
            "BuilderFunctionLogs",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

        function = lambda_.Function(
            self,
            "BuilderFunction",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(builder_dir),
            # Chosen for CPU: extraction and graph assembly are compute-bound,
            # and Lambda scales vCPU with memory. Also gives DuckDB room to
            # sort without spilling.
            memory_size=3008,
            timeout=Duration.minutes(14),
            # DuckDB writes its extensions and spill files here, and terrain
            # tiles are cached across invocations on a warm container.
            ephemeral_storage_size=Size.gibibytes(2),
            tracing=lambda_.Tracing.ACTIVE,
            log_group=log_group,
            # One extraction at a time. Overture's bucket is a shared public
            # resource, and this also bounds the cost of a burst of requests
            # for uncovered cities.
            reserved_concurrent_executions=3,
            environment={
                "LOG_LEVEL": log_level,
                "ENV_NAME": self.env_name,
                "PYTHONUNBUFFERED": "1",
                # DuckDB resolves a writable home for its extension cache.
                "HOME": "/tmp",
                # Without this the builder's catalogue is disabled: every
                # progress write silently no-ops and the upload fails on an
                # empty bucket name, after the extraction has already run. The
                # first on-demand build failed exactly that way.
                "REGION_BUCKET": region_bucket.bucket_name,
            },
        )

        # Progress documents and the baked containers both live in this bucket.
        region_bucket.grant_read_write(function)
        return function

    def _distribution(self, bucket: s3.Bucket) -> cloudfront.Distribution:
        headers = self._security_headers()
        # One origin shared by both behaviours. Constructing it per behaviour
        # would make CDK emit two origins and two Origin Access Controls for
        # the same bucket — harmless but redundant, and it muddies the answer
        # to "what can CloudFront reach".
        origin = origins.S3BucketOrigin.with_origin_access_control(bucket)
        return cloudfront.Distribution(
            self,
            "SiteDistribution",
            comment=f"stepwise-{self.env_name} static site",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origin,
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
                    origin=origin,
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
        region_bucket: s3.Bucket,
        builder: lambda_.Function | None,
    ) -> lambda_.Function:
        log_group = logs.LogGroup(
            self,
            "ApiFunctionLogs",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

        function = lambda_.Function(
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
                "REGION_BUCKET": region_bucket.bucket_name,
                **({"BUILDER_FUNCTION_NAME": builder.function_name} if builder else {}),
                # Deploy-time configuration, last so it can override defaults.
                **self.parameters,
            },
        )

        # The API reads cached regions and writes status documents — the
        # conditional PutObject that claims a build is the reason it needs
        # write access at all.
        region_bucket.grant_read_write(function)
        if builder is not None:
            builder.grant_invoke(function)

        return function

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
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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
