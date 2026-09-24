from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from xc_platform.cli.agentcore_platform_version import (
    AgentRuntimeUpdateError,
    ensure_platform_version,
)


@dataclass
class FakeAgentCoreControlClient:
    """In-memory stand-in for the bedrock-agentcore-control boto3 client.

    ``statuses_after_update`` lets a test script a sequence of statuses
    returned on successive get_agent_runtime polls after an update, so the
    polling loop itself can be exercised deterministically.
    """

    runtime: dict[str, Any]
    statuses_after_update: list[str] = field(default_factory=lambda: ["READY"])
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    _poll_index: int = -1  # -1 means "no update issued yet"

    def get_agent_runtime(self, *, agentRuntimeId: str) -> dict[str, Any]:  # noqa: N803
        if self._poll_index < 0:
            return dict(self.runtime)
        status = self.statuses_after_update[
            min(self._poll_index, len(self.statuses_after_update) - 1)
        ]
        self._poll_index += 1
        return {**self.runtime, "status": status}

    def update_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        self.runtime = {**self.runtime, "platformVersion": kwargs["platformVersion"]}
        self._poll_index = 0
        return {}


def _base_runtime(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agentRuntimeId": "my-agent-ABCDE12345",
        "roleArn": "arn:aws:iam::111122223333:role/AgentExecutionRole",
        "agentRuntimeArtifact": {
            "containerConfiguration": {
                "containerUri": "111122223333.dkr.ecr.us-east-1.amazonaws.com/my-agent"
            }
        },
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "status": "READY",
        "platformVersion": "V1",
    }
    base.update(overrides)
    return base


def _instant_sleep(_seconds: float) -> None:
    return None


def test_no_op_when_already_on_the_target_platform_version() -> None:
    client = FakeAgentCoreControlClient(runtime=_base_runtime(platformVersion="V2"))
    result = ensure_platform_version(
        client, "my-agent-ABCDE12345", platform_version="V2"
    )
    assert result["platformVersion"] == "V2"
    assert client.update_calls == []


def test_updates_and_polls_until_ready() -> None:
    client = FakeAgentCoreControlClient(
        runtime=_base_runtime(),
        statuses_after_update=["UPDATING", "UPDATING", "READY"],
    )
    result = ensure_platform_version(
        client,
        "my-agent-ABCDE12345",
        platform_version="V2",
        sleep=_instant_sleep,
    )
    assert result["platformVersion"] == "V2"
    assert result["status"] == "READY"
    assert len(client.update_calls) == 1


def test_update_call_carries_required_and_preserved_fields() -> None:
    client = FakeAgentCoreControlClient(runtime=_base_runtime())
    ensure_platform_version(
        client, "my-agent-ABCDE12345", platform_version="V2", sleep=_instant_sleep
    )
    call = client.update_calls[0]
    assert call["agentRuntimeId"] == "my-agent-ABCDE12345"
    assert call["roleArn"] == client.runtime["roleArn"]
    assert call["agentRuntimeArtifact"] == client.runtime["agentRuntimeArtifact"]
    assert call["networkConfiguration"] == {"networkMode": "PUBLIC"}
    assert call["platformVersion"] == "V2"
    # Fields never present on the runtime are never fabricated.
    assert "description" not in call


def test_raises_on_failed_status() -> None:
    client = FakeAgentCoreControlClient(
        runtime=_base_runtime(),
        statuses_after_update=["UPDATING", "UPDATE_FAILED"],
    )
    with pytest.raises(AgentRuntimeUpdateError, match="UPDATE_FAILED"):
        ensure_platform_version(
            client, "my-agent-ABCDE12345", platform_version="V2", sleep=_instant_sleep
        )


def test_raises_on_poll_timeout() -> None:
    client = FakeAgentCoreControlClient(
        runtime=_base_runtime(),
        statuses_after_update=["UPDATING"],  # never becomes READY
    )
    fake_clock = {"t": 0.0}

    def fake_now() -> float:
        return fake_clock["t"]

    def fake_sleep(seconds: float) -> None:
        fake_clock["t"] += seconds

    with pytest.raises(AgentRuntimeUpdateError, match="did not reach READY"):
        ensure_platform_version(
            client,
            "my-agent-ABCDE12345",
            platform_version="V2",
            poll_interval_seconds=5.0,
            poll_timeout_seconds=12.0,
            sleep=fake_sleep,
            now=fake_now,
        )
