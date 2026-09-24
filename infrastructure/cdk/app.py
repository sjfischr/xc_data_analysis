#!/usr/bin/env python3
"""CDK app entrypoint. ``StorageStack`` is synth-only (see its module
docstring). ``WebStack`` (Task 15.2/18.1) is deployed for real this
session, with explicit owner authorization recorded 2026-09-22 after Task
17.4 acceptance -- see ``docs/operations-and-acceptance-readiness.md``.
"""

from __future__ import annotations

import os

import aws_cdk as cdk

from stacks.agent_stack import AgentStack
from stacks.ops_stack import OpsStack
from stacks.storage_stack import StorageStack
from stacks.web_stack import WebStack

app = cdk.App()

# Environment-agnostic for synth (no account/region baked in here, so
# `cdk synth` never needs real AWS credentials). A real deploy sets
# CDK_DEFAULT_ACCOUNT/CDK_DEFAULT_REGION (us-east-1 per design.md section
# 6.1) in its own environment -- this app reads them if present but never
# requires them for synth.
env = (
    cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    )
    if os.environ.get("CDK_DEFAULT_ACCOUNT")
    else None
)

storage = StorageStack(app, "XcPlatform-Storage", env=env)

# `-c deployApiService=false` (Task 18.1) defers the App Runner service to a
# second deploy, after an image has been pushed to the ECR repository this
# same stack creates -- see WebStack's own docstring on `deploy_api_service`
# for why a single-pass deploy of both is unsafe on a first-ever apply.
deploy_api_service = app.node.try_get_context("deployApiService")
deploy_api_service = str(deploy_api_service).lower() != "false"

# Task 19.2. `-c deployAgentRuntime=false` defers the runtime until the agent image is
# pushed (same two-pass pattern as the API service); `-c agentModelId=...`
# switches the model without a code change; `-c agentImageTag=<tag>` points the
# runtime at a newly pushed image (the runtime ignores a re-pushed :latest).
deploy_agent_runtime = (
    str(app.node.try_get_context("deployAgentRuntime")).lower() != "false"
)
agent = AgentStack(
    app,
    "XcPlatform-Agent",
    env=env,
    data_bucket_name=storage.data_bucket.bucket_name,
    deploy_runtime=deploy_agent_runtime,
    image_tag=app.node.try_get_context("agentImageTag") or "latest",
    **(
        {"model_id": app.node.try_get_context("agentModelId")}
        if app.node.try_get_context("agentModelId")
        else {}
    ),
)

web = WebStack(
    app,
    "XcPlatform-Web",
    env=env,
    data_bucket_name=storage.data_bucket.bucket_name,
    web_source_dir=os.environ.get("XC_WEB_SOURCE_DIR"),
    deploy_api_service=deploy_api_service,
    agent_runtime_arn=agent.runtime_arn,
)

# Task 19.5: alarms and spend guards. `-c alertEmail=...` subscribes an
# address to the alert topic (confirm it from the email AWS sends).
OpsStack(
    app,
    "XcPlatform-Ops",
    env=env,
    api_service_name=(
        cdk.Fn.select(1, cdk.Fn.split("/", web.api_service.attr_service_arn))
        if web.api_service is not None
        else None
    ),
    api_service_id=web.api_service.attr_service_id if web.api_service else None,
    alert_email=app.node.try_get_context("alertEmail"),
)

app.synth()
