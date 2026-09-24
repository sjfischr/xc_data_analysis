"""One chat turn with conversation memory (Task 19.2): load this actor's
session history, stream the turn, and store the question and final answer.
Shared by the AgentCore entrypoint and the API's in-process invoker, so the
two runtimes behave identically."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from xc_platform.agents.chat_stream import stream_turn
from xc_platform.agents.code_interpreter import PythonExecutor
from xc_platform.agents.conversation_memory import ConversationStore
from xc_platform.db.publication.reader import SnapshotReader

MAX_PROMPT_CHARS = 2000
_ACTOR_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


class ChatRequestError(ValueError):
    pass


def validate_chat_request(
    actor_id: Any, session_id: Any, prompt: Any
) -> tuple[str, str, str]:
    """``actor_id`` must be the HMAC hex digest the API derives
    (agents/identity.py) -- never a raw subject or email."""
    if not isinstance(actor_id, str) or not _ACTOR_ID_RE.match(actor_id):
        raise ChatRequestError("actor_id must be a 64-character hex digest")
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise ChatRequestError("session_id must be 8-100 characters of [A-Za-z0-9_-]")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ChatRequestError("prompt must be a non-empty string")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ChatRequestError(f"prompt must be at most {MAX_PROMPT_CHARS} characters")
    return actor_id, session_id, prompt.strip()


async def run_chat_turn(
    *,
    snapshot_reader: SnapshotReader,
    store: ConversationStore,
    actor_id: str,
    session_id: str,
    prompt: str,
    model: Any | None = None,
    model_id: str | None = None,
    python_executor_factory: Callable[[], PythonExecutor] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    history = store.load(actor_id, session_id)
    answer: str | None = None
    async for event in stream_turn(
        snapshot_reader=snapshot_reader,
        prompt=prompt,
        history=history,
        model=model,
        model_id=model_id,
        python_executor=python_executor_factory() if python_executor_factory else None,
    ):
        if event.get("type") == "final":
            answer = str(event.get("markdown", ""))
        yield event
    if answer:
        store.append(actor_id, session_id, prompt, answer)
