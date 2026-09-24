"""Synthesis tests and policy assertions for AgentStack (Task 19.2)."""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from stacks.agent_stack import AgentStack
from stacks.storage_stack import StorageStack
from stacks.web_stack import WebStack


def _stacks(*, deploy_runtime: bool = True) -> tuple[Template, Template]:
    app = cdk.App()
    storage = StorageStack(app, "TestStorageStack")
    agent = AgentStack(
        app,
        "TestAgentStack",
        data_bucket_name=storage.data_bucket.bucket_name,
        deploy_runtime=deploy_runtime,
    )
    web = WebStack(
        app,
        "TestWebStack",
        data_bucket_name=storage.data_bucket.bucket_name,
        agent_runtime_arn=agent.runtime_arn,
    )
    return Template.from_stack(agent), Template.from_stack(web)


def test_code_interpreter_is_sandboxed_with_no_role() -> None:
    agent, _ = _stacks()
    resources = agent.find_resources("AWS::BedrockAgentCore::CodeInterpreterCustom")
    [sandbox] = resources.values()
    assert sandbox["Properties"]["NetworkConfiguration"] == {"NetworkMode": "SANDBOX"}
    assert "ExecutionRoleArn" not in sandbox["Properties"]


def test_memory_has_no_long_term_strategies() -> None:
    agent, _ = _stacks()
    [memory] = agent.find_resources("AWS::BedrockAgentCore::Memory").values()
    assert "MemoryStrategies" not in memory["Properties"]
    assert memory["Properties"]["EventExpiryDuration"] == 30


def test_runtime_uses_the_default_iam_authorizer_and_passes_wiring_env() -> None:
    agent, _ = _stacks()
    [runtime] = agent.find_resources("AWS::BedrockAgentCore::Runtime").values()
    props = runtime["Properties"]
    # No CUSTOM_JWT: only IAM-signed calls from the API reach the runtime.
    assert "AuthorizerConfiguration" not in props
    assert set(props["EnvironmentVariables"]) == {
        "XC_SNAPSHOT_BUCKET",
        "XC_MEMORY_ID",
        "XC_CODE_INTERPRETER_ID",
        "XC_MODEL_ID",
    }


def test_runtime_role_cannot_write_the_data_bucket() -> None:
    agent, _ = _stacks()
    rendered = json.dumps(agent.to_json())
    assert "s3:PutObject" not in rendered
    assert "s3:DeleteObject" not in rendered


def test_runtime_is_deferred_until_an_image_exists() -> None:
    agent, web = _stacks(deploy_runtime=False)
    agent.resource_count_is("AWS::BedrockAgentCore::Runtime", 0)
    assert "InvokeAgentRuntime" not in json.dumps(web.to_json())


def test_only_the_api_role_may_invoke_the_runtime() -> None:
    _, web = _stacks()
    web.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Action": "bedrock-agentcore:InvokeAgentRuntime",
                                    "Sid": "InvokeAnalyticsAgent",
                                }
                            )
                        ]
                    )
                }
            )
        },
    )
    assert "XC_AGENT_RUNTIME_ARN" in json.dumps(web.to_json())
