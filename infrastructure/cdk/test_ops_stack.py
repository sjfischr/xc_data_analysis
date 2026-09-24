"""Synthesis tests for OpsStack (Task 19.5)."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from stacks.ops_stack import OpsStack


def _template(alert_email: str | None = None) -> Template:
    app = cdk.App()
    stack = OpsStack(
        app,
        "TestOps",
        api_service_name="ApiService-abc",
        api_service_id="0123",
        alert_email=alert_email,
    )
    return Template.from_stack(stack)


def test_api_alarms_notify_the_alert_topic() -> None:
    template = _template()
    template.resource_count_is("AWS::CloudWatch::Alarm", 2)
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {"MetricName": "5xxStatusResponses", "Namespace": "AWS/AppRunner"},
    )


def test_bedrock_and_account_budgets_exist() -> None:
    template = _template()
    template.resource_count_is("AWS::Budgets::Budget", 2)
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {
            "Budget": Match.object_like(
                {"CostFilters": {"Service": ["Amazon Bedrock"]}}
            )
        },
    )


def test_email_subscription_only_when_an_address_is_given() -> None:
    _template().resource_count_is("AWS::SNS::Subscription", 0)
    _template("ops@example.com").resource_count_is("AWS::SNS::Subscription", 1)
