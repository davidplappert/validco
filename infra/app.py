#!/usr/bin/env python3
"""CDK entry point.

Only one environment exists — `dev`, deployed from `main` by GitHub Actions.
That is the whole deployment story on purpose: one branch, one account, one
stack, and no way to push infrastructure from a laptop (the deploy role trusts
this repository's GitHub OIDC identity and nothing else).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# bundle.py sits beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aws_cdk as cdk
from bundle import build as build_builder_bundle
from validco_infra.stack import StepWiseStack

ROOT = Path(__file__).resolve().parents[1]

app = cdk.App()

env_name = app.node.try_get_context("envName") or os.environ.get("ENV_NAME", "dev")
app_version = app.node.try_get_context("appVersion") or os.environ.get("APP_VERSION", "dev")
log_level = app.node.try_get_context("logLevel") or os.environ.get("LOG_LEVEL", "DEBUG")

# Configuration resolved from SSM by the deploy workflow and passed in as
# context, so the Lambda reads plain environment variables instead of calling
# SSM on every cold start. Empty is the normal case: this app has no secrets.
raw_parameters = app.node.try_get_context("parameters") or os.environ.get("STEPWISE_PARAMETERS")
parameters: dict[str, str] = {}
if raw_parameters:
    parameters = json.loads(raw_parameters) if isinstance(raw_parameters, str) else raw_parameters
    print(f"note: injecting {len(parameters)} parameter(s) from SSM into the Lambda environment")

# The web bundle is optional so `cdk synth` works before the frontend is built —
# useful in CI, where synth runs as a fast validation step of its own.
web_dir = ROOT / "web" / "out"
if not (web_dir / "index.html").exists():
    print(f"note: {web_dir} has no index.html; deploying API and config.json only")
    web_dir = None

# The region builder carries native wheels, staged here rather than with CDK's
# Docker bundling — this project deploys without a container runtime. Skipping
# it is supported so `cdk synth` stays fast when only the API changed.
builder_dir: Path | None = None
if os.environ.get("SKIP_BUILDER_BUNDLE", "").lower() not in ("1", "true", "yes"):
    builder_dir = build_builder_bundle(ROOT, ROOT / "cdk.out" / "builder-bundle")

StepWiseStack(
    app,
    f"stepwise-{env_name}",
    env_name=env_name,
    api_dir=str(ROOT / "api"),
    web_dir=str(web_dir) if web_dir else None,
    builder_dir=str(builder_dir) if builder_dir else None,
    app_version=app_version,
    log_level=log_level,
    parameters=parameters,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    description=(
        "StepWise — walking routes with elevation and health impact, "
        "built on Overture Maps open data"
    ),
    tags={"project": "validco", "env": env_name, "managed-by": "cdk"},
)

app.synth()
