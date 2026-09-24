"""Synthesis tests and policy assertions for WebStack (Task 15.2/18.1)."""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from stacks.storage_stack import StorageStack
from stacks.web_stack import WebStack


def _template() -> Template:
    app = cdk.App()
    storage = StorageStack(app, "TestStorageStack")
    web = WebStack(
        app,
        "TestWebStack",
        data_bucket_name=storage.data_bucket.bucket_name,
    )
    return Template.from_stack(web)


def test_user_pool_blocks_self_signup() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        {"AdminCreateUserConfig": Match.object_like({"AllowAdminCreateUserOnly": True})},
    )


def test_admin_viewer_and_agent_users_groups_exist() -> None:
    template = _template()
    template.resource_count_is("AWS::Cognito::UserPoolGroup", 3)
    template.has_resource_properties(
        "AWS::Cognito::UserPoolGroup", {"GroupName": "agent-users"}
    )
    template.has_resource_properties("AWS::Cognito::UserPoolGroup", {"GroupName": "admin"})
    template.has_resource_properties("AWS::Cognito::UserPoolGroup", {"GroupName": "viewer"})


def test_app_client_has_no_secret() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient", {"GenerateSecret": False}
    )


def test_frontend_bucket_blocks_all_public_access() -> None:
    template = _template()
    buckets = template.find_resources(
        "AWS::S3::Bucket",
        {"Properties": {"PublicAccessBlockConfiguration": Match.any_value()}},
    )
    assert buckets, "expected the frontend bucket to have a public access block"
    for bucket in buckets.values():
        config = bucket["Properties"]["PublicAccessBlockConfiguration"]
        assert config == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }


def test_app_runner_health_check_targets_the_real_health_endpoint() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::AppRunner::Service",
        {
            "HealthCheckConfiguration": Match.object_like(
                {"Protocol": "HTTP", "Path": "/api/v1/system/health"}
            )
        },
    )


def test_app_runner_has_exactly_one_instance_running() -> None:
    """Correctness, not just cost: the in-memory session/ticket-replay
    store (run_production_api.py's documented limitation) requires exactly
    one instance -- App Runner CfnService has no min/max instance count
    property to assert here directly (that's set by an
    AutoScalingConfiguration this stack does not attach, which is what
    keeps it at App Runner's default of a single active instance
    concurrency-scaled, not multi-instance-scaled)."""
    template = _template()
    services = template.find_resources("AWS::AppRunner::Service")
    assert len(services) == 1


def test_instance_role_ssm_grant_is_scoped_to_the_xc_platform_prefix() -> None:
    """The synthesized resource ARN is exactly
    ``arn:aws:ssm:<region>:<account>:parameter/xc-platform/*`` (confirmed
    via the JSON template directly -- CDK's ``Match`` array-position
    matchers don't line up cleanly against a multi-element ``Fn::Join``,
    so this checks the rendered template text instead of an object
    matcher)."""
    template = _template()
    rendered = json.dumps(template.to_json())
    assert "ssm:GetParameter" in rendered
    assert "parameter/xc-platform/*" in rendered


def test_cloudfront_rewrites_clean_urls_to_their_static_export_file() -> None:
    """Regression test for the "every new page bounces back to /dashboard"
    bug found live, 2026-09-23: Next.js static export (no trailingSlash)
    writes each route as `<route>.html`, and an S3 origin behind Origin
    Access Control never resolves a directory index on its own -- without
    a viewer-request rewrite, a real navigation to a clean URL like
    `/standings` 404s at the origin and falls back to the generic
    `index.html` shell."""
    template = _template()
    template.has_resource_properties(
        "AWS::CloudFront::Function",
        {"FunctionConfig": Match.object_like({"Runtime": "cloudfront-js-2.0"})},
    )
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": Match.object_like(
                {
                    "DefaultCacheBehavior": Match.object_like(
                        {
                            "FunctionAssociations": Match.array_with(
                                [
                                    Match.object_like(
                                        {"EventType": "viewer-request"}
                                    )
                                ]
                            )
                        }
                    )
                }
            )
        },
    )
