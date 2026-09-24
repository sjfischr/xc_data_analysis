"""How the API reaches the analytics agent (Task 19.2) and whether it may
(Task 19.4).

Two interchangeable invokers yield the same event stream
(:mod:`xc_platform.agents.chat_stream`):

- :class:`AgentCoreRuntimeInvoker` (production): SigV4-signed
  ``InvokeAgentRuntime`` with the API's own instance role, passing the
  server-derived ``actor_id``. The runtime session id is per conversation,
  so AgentCore keeps one warm microVM per conversation and never shares
  one across users.
- :class:`InProcessAgentInvoker` (local development, tests, and an
  emergency fallback): runs the same chat service in this process.

:class:`AgentSwitch` is the global kill switch -- SSM parameter
``XC_AGENT_ENABLED`` (``/xc-platform/XC_AGENT_ENABLED``), re-read at most
every 30 seconds so flipping it takes effect without a redeploy.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from xc_platform.agents.chat_service import run_chat_turn
from xc_platform.agents.code_interpreter import PythonExecutor
from xc_platform.agents.conversation_memory import (
    ConversationStore,
    InMemoryConversationStore,
)
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.security.config import get_secret

SWITCH_TTL_SECONDS = 30.0
_END = object()


class AgentInvoker(Protocol):
    def stream(
        self, *, actor_id: str, session_id: str, prompt: str
    ) -> AsyncIterator[dict[str, Any]]: ...

    async def delete_memory(self, *, actor_id: str) -> int: ...


class InProcessAgentInvoker:
    def __init__(
        self,
        *,
        snapshot_reader: SnapshotReader,
        store: ConversationStore | None = None,
        model_factory: Callable[[], Any] | None = None,
        python_executor_factory: Callable[[], PythonExecutor] | None = None,
    ) -> None:
        self._reader = snapshot_reader
        self._store = store or InMemoryConversationStore()
        self._model_factory = model_factory
        self._executor_factory = python_executor_factory

    async def stream(
        self, *, actor_id: str, session_id: str, prompt: str
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in run_chat_turn(
            snapshot_reader=self._reader,
            store=self._store,
            actor_id=actor_id,
            session_id=session_id,
            prompt=prompt,
            model=self._model_factory() if self._model_factory else None,
            python_executor_factory=self._executor_factory,
        ):
            yield event

    async def delete_memory(self, *, actor_id: str) -> int:
        return self._store.delete_actor(actor_id)


def runtime_session_id(actor_id: str, session_id: str) -> str:
    """AgentCore requires 33+ characters; scoping it by actor means two
    users can never land in the same runtime session even if a client
    reused a conversation id."""
    return f"{actor_id[:24]}-{session_id}"


class AgentCoreRuntimeInvoker:
    def __init__(
        self, *, runtime_arn: str, region: str, client: Any | None = None
    ) -> None:
        self._runtime_arn = runtime_arn
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "bedrock-agentcore",
                region_name=region,
                config=Config(read_timeout=300, retries={"max_attempts": 2}),
            )
        self._client = client

    def _invoke_lines(self, payload: dict[str, Any], session: str) -> Any:
        response = self._client.invoke_agent_runtime(
            agentRuntimeArn=self._runtime_arn,
            runtimeSessionId=session,
            payload=json.dumps(payload).encode("utf-8"),
            contentType="application/json",
            accept="text/event-stream",
        )
        body = response["response"]
        content_type = response.get("contentType", "")
        if "text/event-stream" in content_type:
            for raw in body.iter_lines():
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data:
                        yield json.loads(data)
        else:
            # A non-streaming handler reply (e.g. an early validation error).
            text = body.read().decode("utf-8")
            if text.strip():
                parsed = json.loads(text)
                yield from parsed if isinstance(parsed, list) else [parsed]

    async def _stream_payload(
        self, payload: dict[str, Any], session: str
    ) -> AsyncIterator[dict[str, Any]]:
        """boto3 is blocking; read the stream on a worker thread and hand
        events to the event loop as they arrive."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def pump() -> None:
            try:
                for event in self._invoke_lines(payload, session):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception as error:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "error", "message": _public_invoke_error(error)},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _END)

        threading.Thread(target=pump, daemon=True).start()
        while True:
            item = await queue.get()
            if item is _END:
                return
            yield item

    async def stream(
        self, *, actor_id: str, session_id: str, prompt: str
    ) -> AsyncIterator[dict[str, Any]]:
        payload = {
            "action": "chat",
            "actor_id": actor_id,
            "session_id": session_id,
            "prompt": prompt,
        }
        async for event in self._stream_payload(
            payload, runtime_session_id(actor_id, session_id)
        ):
            yield event

    async def delete_memory(self, *, actor_id: str) -> int:
        deleted = 0
        async for event in self._stream_payload(
            {"action": "delete_memory", "actor_id": actor_id},
            runtime_session_id(actor_id, "memory-maintenance"),
        ):
            if event.get("type") == "memory_deleted":
                deleted = int(event.get("events", 0))
            elif event.get("type") == "error":
                raise RuntimeError(str(event.get("message")))
        return deleted


def _public_invoke_error(error: Exception) -> str:
    text = f"{type(error).__name__}: {error}"
    if "Throttl" in text:
        return "The assistant is busy right now. Please try again in a moment."
    return "The assistant is unavailable right now. Please try again shortly."


class AgentSwitch:
    """Global on/off switch. Missing parameter = on; any value other than
    true/1/yes/on = off. A read failure keeps the last known value (or on,
    at first read), so an SSM hiccup never takes the feature down."""

    def __init__(self, reader: Callable[[], str] | None = None) -> None:
        self._reader = reader or (
            lambda: get_secret("XC_AGENT_ENABLED", default="true")
        )
        self._value = True
        self._checked_at: float | None = None
        self._lock = threading.Lock()

    def enabled(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._checked_at is None or now - self._checked_at >= SWITCH_TTL_SECONDS:
                self._checked_at = now
                try:
                    raw = self._reader().strip().lower()
                    self._value = raw in {"true", "1", "yes", "on"}
                except Exception:  # noqa: S110 -- keep the last known value
                    pass
            return self._value
