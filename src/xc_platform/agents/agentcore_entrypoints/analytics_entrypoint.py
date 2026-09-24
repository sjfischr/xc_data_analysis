"""AgentCore Runtime entrypoint for the analytics agent (Task 11.5, reworked
by Task 19.2; design.md sections 12.2, 12.3, 12.6).

**Identity model (owner decision, 2026-09-23).** The runtime keeps AgentCore's
default IAM (SigV4) authorizer, and only the API's App Runner instance role
is granted ``bedrock-agentcore:InvokeAgentRuntime`` on it (CDK,
``agent_stack.py``). The API authenticates the user with its own
server-side session and passes the pseudonymous ``actor_id`` it derived
(HMAC of the Cognito subject) in the payload. Because no browser can reach
this runtime directly, the payload's ``actor_id`` is as trustworthy as the
API's session -- this closes the Task 3.5 actor-isolation gap by design
instead of relying on a browser-held JWT the cookie-session architecture no
longer has.

Payload contract::

    {"action": "chat", "actor_id": <64 hex>, "session_id": str, "prompt": str}
    {"action": "delete_memory", "actor_id": <64 hex>}

A chat request streams the event sequence documented in
:mod:`xc_platform.agents.chat_stream`; AgentCore delivers an async
generator's items to the caller as Server-Sent Events.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from xc_platform.agents.chat_service import (
    ChatRequestError,
    run_chat_turn,
    validate_chat_request,
)
from xc_platform.agents.code_interpreter import (
    DEFAULT_INTERPRETER_ID,
    AgentCoreCodeInterpreterExecutor,
)
from xc_platform.agents.conversation_memory import (
    AgentCoreConversationStore,
    ConversationStore,
    InMemoryConversationStore,
)
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import Boto3S3Client

# --- Startup phase (module scope): nothing here reads per-request state,
# the clock, or a credential value (boto3 resolves credentials lazily per
# call), so it is safe to capture once and share (design.md 12.6).
app = BedrockAgentCoreApp()
log = app.logger

REGION = os.environ.get("AWS_REGION", "us-east-1")
_snapshot_reader = SnapshotReader(
    Boto3S3Client.create(bucket=os.environ["XC_SNAPSHOT_BUCKET"], region=REGION),
    # Ephemeral per-instance cache; re-verified on every refresh, never
    # authoritative (design.md 12.6).
    cache_dir=Path(os.environ.get("XC_SNAPSHOT_CACHE_DIR", "/tmp/xc-snapshot-cache")),  # noqa: S108
)
_memory_id = os.environ.get("XC_MEMORY_ID")
_store: ConversationStore = (
    AgentCoreConversationStore(memory_id=_memory_id, region=REGION)
    if _memory_id
    else InMemoryConversationStore()
)
_interpreter_id = os.environ.get("XC_CODE_INTERPRETER_ID", DEFAULT_INTERPRETER_ID)


def _executor() -> AgentCoreCodeInterpreterExecutor:
    return AgentCoreCodeInterpreterExecutor(
        region=REGION, interpreter_id=_interpreter_id
    )


@app.entrypoint
async def invoke(
    payload: dict[str, Any], context: object
) -> AsyncIterator[dict[str, Any]]:
    action = payload.get("action", "chat")
    if action == "delete_memory":
        actor_id = payload.get("actor_id")
        try:
            validate_chat_request(actor_id, "placeholder", "x")
        except ChatRequestError as error:
            yield {"type": "error", "message": str(error)}
            return
        deleted = _store.delete_actor(str(actor_id))
        log.info("memory deleted for actor %s: %d events", str(actor_id)[:8], deleted)
        yield {"type": "memory_deleted", "events": deleted}
        return

    try:
        actor_id, session_id, prompt = validate_chat_request(
            payload.get("actor_id"), payload.get("session_id"), payload.get("prompt")
        )
    except ChatRequestError as error:
        yield {"type": "error", "message": str(error)}
        return

    log.info("analytics chat actor=%s session=%s", actor_id[:8], session_id[:8])
    async for event in run_chat_turn(
        snapshot_reader=_snapshot_reader,
        store=_store,
        actor_id=actor_id,
        session_id=session_id,
        prompt=prompt,
        python_executor_factory=_executor,
    ):
        yield event


if __name__ == "__main__":
    app.run()
