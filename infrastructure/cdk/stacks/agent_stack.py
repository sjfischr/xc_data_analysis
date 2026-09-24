"""Analytics agent on Amazon Bedrock AgentCore (Task 19.2).

- **ECR repository** for the slim arm64 agent image (``Dockerfile.agent``).
- **Memory**: short-term conversation events only (no long-term
  strategies -- release 1 stores question/answer text per session, 30-day
  expiry; users can delete theirs via ``DELETE /api/v1/chat/memory``).
- **Code Interpreter** (custom): ``SANDBOX`` network mode and no execution
  role, so model-written Python has neither network access nor AWS
  credentials. Data reaches it only as a CSV the agent's read-only SQL
  produced (agents/code_interpreter.py).
- **Runtime**: the agent container with AgentCore's default IAM (SigV4)
  authorizer. Nothing but the API's App Runner role is granted
  ``InvokeAgentRuntime`` (web_stack.py), and the API passes the
  pseudonymous actor id it derived from its own session -- the owner-
  approved design that closes the Task 3.5 actor-isolation gap.

The AgentCore resource types are newer than this project's pinned
aws-cdk-lib (2.216.0 has no ``aws_bedrockagentcore`` module), so they are
declared as raw ``CfnResource``s; property names were checked against the
live CloudFormation registry schemas (2026-09-23).

``deploy_runtime=False`` (``-c deployAgentRuntime=false``) creates
everything except the runtime, so the image can be pushed before the
runtime references it -- the same two-pass pattern WebStack uses for App
Runner.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, CfnResource, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

MEMORY_EVENT_EXPIRY_DAYS = 30
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


class AgentStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data_bucket_name: str,
        deploy_runtime: bool = True,
        model_id: str = DEFAULT_MODEL_ID,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        self.repository = ecr.Repository(
            self,
            "AgentRepository",
            image_scan_on_push=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=10)],
        )

        self.memory = CfnResource(
            self,
            "ChatMemory",
            type="AWS::BedrockAgentCore::Memory",
            properties={
                "Name": "xc_platform_chat",
                "Description": "Ask the Data conversation turns (short-term only)",
                "EventExpiryDuration": MEMORY_EVENT_EXPIRY_DAYS,
            },
        )
        self.memory_id = self.memory.get_att("MemoryId").to_string()
        self.memory_arn = self.memory.get_att("MemoryArn").to_string()

        self.code_interpreter = CfnResource(
            self,
            "AnalysisSandbox",
            type="AWS::BedrockAgentCore::CodeInterpreterCustom",
            properties={
                "Name": "xc_platform_sandbox",
                "Description": "Isolated Python for agent statistics; no network, no role",
                "NetworkConfiguration": {"NetworkMode": "SANDBOX"},
            },
        )
        self.code_interpreter_id = self.code_interpreter.get_att(
            "CodeInterpreterId"
        ).to_string()
        self.code_interpreter_arn = self.code_interpreter.get_att(
            "CodeInterpreterArn"
        ).to_string()

        self.runtime_role = iam.Role(
            self,
            "AgentRuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"
                    },
                },
            ),
            description="Least-privilege execution role for the analytics agent runtime",
        )
        self.repository.grant_pull(self.runtime_role)
        s3.Bucket.from_bucket_name(self, "ImportedDataBucket", data_bucket_name).grant_read(
            self.runtime_role
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeClaudeModels",
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/us.anthropic.claude-*",
                ],
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="ConversationMemory",
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:DeleteEvent",
                ],
                resources=[self.memory_arn],
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="PythonSandbox",
                actions=[
                    "bedrock-agentcore:StartCodeInterpreterSession",
                    "bedrock-agentcore:InvokeCodeInterpreter",
                    "bedrock-agentcore:StopCodeInterpreterSession",
                    "bedrock-agentcore:GetCodeInterpreterSession",
                ],
                resources=[self.code_interpreter_arn],
            )
        )
        self.runtime_role.add_to_policy(
            iam.PolicyStatement(
                sid="Observability",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            )
        )

        self.runtime: CfnResource | None = None
        self.runtime_arn: str | None = None
        if deploy_runtime:
            self.runtime = CfnResource(
                self,
                "AnalyticsRuntime",
                type="AWS::BedrockAgentCore::Runtime",
                properties={
                    "AgentRuntimeName": "xc_platform_analytics",
                    "Description": "Ask the Data analytics agent",
                    "AgentRuntimeArtifact": {
                        "ContainerConfiguration": {
                            "ContainerUri": f"{self.repository.repository_uri}:latest"
                        }
                    },
                    "RoleArn": self.runtime_role.role_arn,
                    "NetworkConfiguration": {"NetworkMode": "PUBLIC"},
                    "ProtocolConfiguration": "HTTP",
                    # A conversation's microVM is reused while the user keeps
                    # asking; after 15 idle minutes it is released (billing
                    # stops; the conversation itself lives in Memory).
                    "LifecycleConfiguration": {
                        "IdleRuntimeSessionTimeout": 900,
                        "MaxLifetime": 28800,
                    },
                    "EnvironmentVariables": {
                        # AWS_REGION is provided by the runtime itself.
                        "XC_SNAPSHOT_BUCKET": data_bucket_name,
                        "XC_MEMORY_ID": self.memory_id,
                        "XC_CODE_INTERPRETER_ID": self.code_interpreter_id,
                        "XC_MODEL_ID": model_id,
                    },
                },
            )
            self.runtime.node.add_dependency(self.runtime_role)
            self.runtime_arn = self.runtime.get_att("AgentRuntimeArn").to_string()
            CfnOutput(self, "AgentRuntimeArn", value=self.runtime_arn)

        CfnOutput(self, "AgentRepositoryUri", value=self.repository.repository_uri)
        CfnOutput(self, "MemoryId", value=self.memory_id)
        CfnOutput(self, "CodeInterpreterId", value=self.code_interpreter_id)
