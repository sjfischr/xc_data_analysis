"""Operational alarms and spend guards (Task 19.5).

- One SNS topic for every alert. An email subscription is added only when
  the operator passes ``-c alertEmail=you@example.com`` at deploy time --
  no address is stored in the repository.
- The API's App Runner service: 5xx responses and p99 latency.
- Monthly budgets: Amazon Bedrock (model tokens) and the whole account,
  each alerting at 80% of actual spend and 100% of forecast.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct


class OpsStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        api_service_name: str | None,
        api_service_id: str | None,
        alert_email: str | None = None,
        bedrock_monthly_usd: float = 25.0,
        account_monthly_usd: float = 75.0,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        self.topic = sns.Topic(self, "AlertTopic", display_name="XC Data alerts")
        # AWS Budgets publishes notifications to this topic.
        self.topic.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                resources=[self.topic.topic_arn],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            )
        )
        if alert_email:
            self.topic.add_subscription(subscriptions.EmailSubscription(alert_email))
        action = cloudwatch_actions.SnsAction(self.topic)

        if api_service_name and api_service_id:
            dimensions = {"ServiceName": api_service_name, "ServiceID": api_service_id}
            errors = cloudwatch.Metric(
                namespace="AWS/AppRunner",
                metric_name="5xxStatusResponses",
                dimensions_map=dimensions,
                statistic="Sum",
                period=Duration.minutes(5),
            )
            cloudwatch.Alarm(
                self,
                "Api5xxAlarm",
                alarm_description="The API returned 5 or more server errors in 5 minutes",
                metric=errors,
                threshold=5,
                evaluation_periods=1,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            ).add_alarm_action(action)
            latency = cloudwatch.Metric(
                namespace="AWS/AppRunner",
                metric_name="RequestLatency",
                dimensions_map=dimensions,
                statistic="p99",
                period=Duration.minutes(5),
            )
            cloudwatch.Alarm(
                self,
                "ApiLatencyAlarm",
                # Chat streams run long by design (up to ~1 min); this
                # watches for the whole API slowing down, not one answer.
                alarm_description="API p99 latency above 60 s for 15 minutes",
                metric=latency,
                threshold=60_000,
                evaluation_periods=3,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            ).add_alarm_action(action)

        def budget(budget_id: str, name: str, amount: float, service: str | None) -> None:
            budgets.CfnBudget(
                self,
                budget_id,
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=name,
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=amount, unit="USD"),
                    cost_filters={"Service": [service]} if service else None,
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            comparison_operator="GREATER_THAN",
                            notification_type=notification_type,
                            threshold=threshold,
                            threshold_type="PERCENTAGE",
                        ),
                        subscribers=[
                            budgets.CfnBudget.SubscriberProperty(
                                address=self.topic.topic_arn, subscription_type="SNS"
                            )
                        ],
                    )
                    for notification_type, threshold in (("ACTUAL", 80), ("FORECASTED", 100))
                ],
            )

        budget("BedrockBudget", "xc-platform-bedrock", bedrock_monthly_usd, "Amazon Bedrock")
        budget("AccountBudget", "xc-platform-account", account_monthly_usd, None)

        CfnOutput(self, "AlertTopicArn", value=self.topic.topic_arn)
