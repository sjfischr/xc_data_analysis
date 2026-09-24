"""Ask the Data: the analytics agent over Server-Sent Events (Task 19.2,
access control Task 19.4).

Replaces the WebSocket route (Task 13.5): App Runner's Envoy front end
rejects WebSocket upgrades before they reach the app (verified live,
2026-09-23 -- a raw RFC 6455 handshake got an empty 403 from ``server:
envoy`` and never appeared in the application log). A ``POST`` answered
with ``text/event-stream`` needs no upgrade, keeps the ordinary cookie
session and double-submit CSRF check, and needs no connection ticket.

Each SSE message is one event from :mod:`xc_platform.agents.chat_stream`,
serialized as ``data: <json>``. A comment line (``: keepalive``) is sent
while the agent is working silently, so proxies never see an idle stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from xc_platform.agents.chat_service import MAX_PROMPT_CHARS
from xc_platform.api.context import AppContext
from xc_platform.api.deps import get_context, require_session
from xc_platform.api.errors import AgentDisabledError, ForbiddenError
from xc_platform.security.session import SessionRecord

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

KEEPALIVE_SECONDS = 10.0


class ChatMessageRequest(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


def _agent_enabled(ctx: AppContext) -> bool:
    return ctx.agent_switch is None or ctx.agent_switch.enabled()


def require_agent_access(
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> SessionRecord:
    if not _agent_enabled(ctx):
        raise AgentDisabledError("Ask the Data is turned off right now.")
    if not session.agent_access:
        raise ForbiddenError("Your account does not have access to Ask the Data.")
    return session


@router.get("/status")
def chat_status(
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, bool]:
    enabled = _agent_enabled(ctx)
    return {
        "enabled": enabled,
        "access": session.agent_access,
        "available": enabled and session.agent_access,
    }


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


async def _with_keepalive(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    iterator = events.__aiter__()
    pending: asyncio.Task[dict[str, Any]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=KEEPALIVE_SECONDS)
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield _sse(event)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


@router.post("/messages")
async def send_message(
    body: ChatMessageRequest,
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_agent_access),
) -> StreamingResponse:
    invoker = ctx.get_agent_invoker()
    events = invoker.stream(
        actor_id=session.actor_id, session_id=body.session_id, prompt=body.prompt
    )
    return StreamingResponse(
        _with_keepalive(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.delete("/memory")
async def delete_memory(
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, int]:
    """Requirement 11.8: a user can erase their own conversation memory.
    Allowed even without current agent access -- deleting your own data
    should never depend on still being entitled to the feature."""
    deleted = await ctx.get_agent_invoker().delete_memory(actor_id=session.actor_id)
    return {"deleted_events": deleted}
