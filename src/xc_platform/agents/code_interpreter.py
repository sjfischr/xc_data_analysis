"""Sandboxed Python execution for the analytics agent (Task 19.2).

Model-written code runs only inside an AgentCore Code Interpreter session
-- a managed, isolated sandbox with no route to this application's
database, credentials, or network (the CDK stack creates a custom
interpreter with ``NetworkMode: SANDBOX``). It is never executed in the
API or agent process: the platform ingests third-party race data, so text
that reaches the model can be adversarial, and in-process execution
(Strands' ``python_repl``) would turn a prompt injection into code
execution on our own server.

The sandbox ships pandas, numpy, scipy, statsmodels, sympy, and matplotlib
(verified live on this account, 2026-09-23). Data reaches it only as a CSV
the agent's own read-only SQL produced.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

DEFAULT_INTERPRETER_ID = "aws.codeinterpreter.v1"
CHART_FILENAME = "chart.png"
MAX_OUTPUT_CHARS = 6000
MAX_IMAGE_BYTES = 600_000
SESSION_TIMEOUT_SECONDS = 300

PREAMBLE = "import matplotlib\nmatplotlib.use('Agg')\n"


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    exit_code: int
    images: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class PythonExecutor(Protocol):
    def run(self, code: str, files: dict[str, str]) -> ExecutionResult: ...

    def close(self) -> None: ...


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (
        text[:MAX_OUTPUT_CHARS] + f"\n…[truncated {len(text) - MAX_OUTPUT_CHARS} chars]"
    )


def _parse_execution(response: dict[str, Any]) -> tuple[str, str, int]:
    stdout, stderr, exit_code = "", "", 0
    for event in response.get("stream", []):
        result = event.get("result")
        if not result:
            continue
        structured = result.get("structuredContent") or {}
        stdout += structured.get("stdout", "")
        stderr += structured.get("stderr", "")
        exit_code = int(structured.get("exitCode", exit_code) or 0)
        if result.get("isError") and not exit_code:
            exit_code = 1
            for item in result.get("content", []):
                if item.get("type") == "text":
                    stderr += item.get("text", "")
    return stdout, stderr, exit_code


class AgentCoreCodeInterpreterExecutor:
    """One sandbox session, started lazily on first use and reused for the
    rest of the agent turn (so a follow-up cell sees earlier variables),
    then stopped by :meth:`close`."""

    def __init__(
        self,
        *,
        region: str,
        interpreter_id: str = DEFAULT_INTERPRETER_ID,
    ) -> None:
        self._region = region
        self._interpreter_id = interpreter_id
        self._client: Any | None = None

    def _session(self) -> Any:
        if self._client is None:
            from bedrock_agentcore.tools.code_interpreter_client import (
                CodeInterpreter,
            )

            client = CodeInterpreter(self._region)
            client.start(
                identifier=self._interpreter_id,
                session_timeout_seconds=SESSION_TIMEOUT_SECONDS,
            )
            self._client = client
        return self._client

    def run(self, code: str, files: dict[str, str]) -> ExecutionResult:
        client = self._session()
        if files:
            client.invoke(
                "writeFiles",
                {"content": [{"path": p, "text": t} for p, t in files.items()]},
            )
        stdout, stderr, exit_code = _parse_execution(
            client.execute_code(PREAMBLE + code)
        )
        images: list[tuple[str, bytes]] = []
        if exit_code == 0 and CHART_FILENAME in code:
            try:
                content = client.download_file(CHART_FILENAME)
                if isinstance(content, bytes) and len(content) <= MAX_IMAGE_BYTES:
                    images.append((CHART_FILENAME, content))
                # Remove it so a later cell's chart is never confused with
                # this one.
                client.execute_code(
                    f"import os\nos.path.exists('{CHART_FILENAME}') and "
                    f"os.remove('{CHART_FILENAME}')"
                )
            except FileNotFoundError:
                pass
        return ExecutionResult(
            stdout=_truncate(stdout),
            stderr=_truncate(stderr),
            exit_code=exit_code,
            images=images,
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.stop()
            except Exception:
                log.warning("code interpreter session stop failed", exc_info=True)
            self._client = None


def image_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
