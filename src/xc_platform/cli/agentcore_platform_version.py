"""Set/confirm ``platformVersion`` on a deployed AgentCore Runtime.

The ``agentcore`` CLI deploys through a generated CDK app
(``agentcore.json``: ``"managedBy": "CDK"``), and neither CDK nor
CloudFormation currently support setting ``platformVersion`` (design.md
section 12.6). This script calls ``bedrock-agentcore-control`` directly via
boto3 instead: read the runtime's current required fields (``roleArn``,
``agentRuntimeArtifact``) and every other configured field, resubmit them
through ``UpdateAgentRuntime`` with the target ``platformVersion``, and poll
``GetAgentRuntime`` until the runtime reaches a terminal status. AWS confirms
that a later CDK-driven update which omits ``platformVersion`` leaves the
runtime on whatever platform version it already has, so this is safe to run
once per runtime, not on every deploy.

Usage::

    python -m xc_platform.cli.agentcore_platform_version <agent-runtime-id> \\
        [--platform-version V2] [--region us-east-1] [--poll-timeout 600]
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any

# Fields UpdateAgentRuntime accepts beyond the two it requires (roleArn,
# agentRuntimeArtifact). Preserved from the current runtime so this call is
# a pure platform-version change, never an accidental reset of the rest of
# the runtime's configuration.
_PRESERVED_OPTIONAL_FIELDS = (
    "networkConfiguration",
    "description",
    "authorizerConfiguration",
    "requestHeaderConfiguration",
    "protocolConfiguration",
    "lifecycleConfiguration",
    "environmentVariables",
    "filesystemConfigurations",
    "capacityProviderConfiguration",
)

_TERMINAL_READY = "READY"


class AgentRuntimeUpdateError(RuntimeError):
    """The runtime did not reach ``READY`` within the poll timeout, or failed."""


def get_runtime(client: Any, agent_runtime_id: str) -> dict[str, Any]:
    result: dict[str, Any] = client.get_agent_runtime(agentRuntimeId=agent_runtime_id)
    return result


def ensure_platform_version(
    client: Any,
    agent_runtime_id: str,
    *,
    platform_version: str = "V2",
    poll_interval_seconds: float = 5.0,
    poll_timeout_seconds: float = 600.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Set ``platform_version`` on ``agent_runtime_id``, polling until terminal.

    A no-op (no ``UpdateAgentRuntime`` call at all) if the runtime is
    already on ``platform_version``. Otherwise updates it, then polls
    ``GetAgentRuntime`` -- a V2 update takes minutes, not seconds, since
    AgentCore Runtime prepares and snapshots the environment.

    Returns the final ``get_agent_runtime`` response. Raises
    :class:`AgentRuntimeUpdateError` if the runtime reaches a ``*_FAILED``
    status, or does not reach ``READY`` within ``poll_timeout_seconds``.
    """
    current = get_runtime(client, agent_runtime_id)
    if current.get("platformVersion") == platform_version:
        return current

    update_kwargs: dict[str, Any] = {
        "agentRuntimeId": agent_runtime_id,
        "roleArn": current["roleArn"],
        "agentRuntimeArtifact": current["agentRuntimeArtifact"],
        "platformVersion": platform_version,
    }
    for field_name in _PRESERVED_OPTIONAL_FIELDS:
        if field_name in current:
            update_kwargs[field_name] = current[field_name]

    client.update_agent_runtime(**update_kwargs)

    deadline = now() + poll_timeout_seconds
    while True:
        response = get_runtime(client, agent_runtime_id)
        status = str(response.get("status", ""))
        if status == _TERMINAL_READY:
            return response
        if status.endswith("FAILED"):
            raise AgentRuntimeUpdateError(
                f"agent runtime {agent_runtime_id!r} reached status "
                f"{status!r} while setting platformVersion="
                f"{platform_version!r}: {response.get('failureReason')}"
            )
        if now() >= deadline:
            raise AgentRuntimeUpdateError(
                f"agent runtime {agent_runtime_id!r} did not reach "
                f"{_TERMINAL_READY} within {poll_timeout_seconds}s "
                f"(last status: {status!r})"
            )
        sleep(poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_runtime_id")
    parser.add_argument("--platform-version", default="V2", choices=["V1", "V2"])
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--poll-timeout", type=float, default=600.0)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    import boto3

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    try:
        result = ensure_platform_version(
            client,
            args.agent_runtime_id,
            platform_version=args.platform_version,
            poll_interval_seconds=args.poll_interval,
            poll_timeout_seconds=args.poll_timeout,
        )
    except AgentRuntimeUpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"{args.agent_runtime_id}: platformVersion="
        f"{result.get('platformVersion')} status={result.get('status')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
