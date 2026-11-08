"""Ask the Data: durable background jobs with authenticated progress polling.

App Runner caps HTTP responses at 120 seconds. Production submits a short
AgentCore invocation, then reads an actor-scoped durable event journal.
Legacy SSE remains a local/test adapter only, not a production execution
path. Existing cookie authentication and CSRF apply to all job mutations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from xc_platform.agents.chat_jobs import (
    JobConflictError,
    JobNotFoundError,
)
from xc_platform.agents.chat_service import MAX_PROMPT_CHARS
from xc_platform.api.agent_invoker import AgentCoreRuntimeInvoker
from xc_platform.api.context import AppContext
from xc_platform.api.deps import get_context, require_session
from xc_platform.api.errors import (
    AgentDisabledError,
    ApiError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from xc_platform.security.session import SessionRecord

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

log = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 10.0
# Legacy local/test SSE backstop; background jobs never use this relay.
TURN_DEADLINE_SECONDS = 110.0
_TERMINAL_EVENTS = frozenset({"final", "error"})
STOPPED_MESSAGE = "The assistant stopped before finishing. Please try again."
TOO_SLOW_MESSAGE = (
    "The assistant is taking too long on this one. Try a narrower question, "
    "or ask again in a moment."
)


class ChatMessageRequest(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


class ChatJobRequest(ChatMessageRequest):
    job_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )


class JobSubmissionError(ApiError):
    status_code = 502
    code = "job_submission_uncertain"


@router.post("/jobs", status_code=202)
async def submit_job(
    body: ChatJobRequest,
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, Any]:
    require_agent_access(ctx, session)
    jobs = ctx.get_chat_jobs()
    try:
        submission = jobs.submit(
            session.actor_id, body.session_id, body.job_id, body.prompt
        )
    except JobConflictError as error:
        raise ConflictError(str(error)) from error
    except ValueError as error:
        raise ValidationError(str(error)) from error
    if submission.job["state"] == "pending":
        try:
            await ctx.get_agent_invoker().submit_job(
                actor_id=session.actor_id,
                session_id=body.session_id,
                prompt=body.prompt,
                job_id=body.job_id,
                jobs=jobs,
            )
        except Exception as error:
            log.exception("chat submission uncertain job=%s", body.job_id)
            raise JobSubmissionError(
                "The question was saved, but its start could not be confirmed. "
                "Reconnect or retry this same job ID; do not create a new submission."
            ) from error
    return jobs.public(jobs.get(session.actor_id, body.job_id))


@router.get("/jobs")
def list_jobs(
    session_id: str = Query(pattern=r"^[A-Za-z0-9_-]{8,100}$"),
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, Any]:
    jobs = ctx.get_chat_jobs()
    return {
        "jobs": [
            jobs.public(record)
            for record in jobs.list_jobs(session.actor_id, session_id)
        ]
    }


@router.get("/jobs/{job_id}")
def poll_job(
    job_id: str,
    after: int = Query(default=0, ge=0),
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, Any]:
    try:
        return ctx.get_chat_jobs().poll(session.actor_id, job_id, after)
    except JobNotFoundError as error:
        raise NotFoundError(str(error)) from error
    except ValueError as error:
        raise ValidationError(str(error)) from error


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_session),
) -> dict[str, Any]:
    try:
        return ctx.get_chat_jobs().cancel(session.actor_id, job_id)
    except JobNotFoundError as error:
        raise NotFoundError(str(error)) from error
    except ValueError as error:
        raise ValidationError(str(error)) from error


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


async def _with_keepalive(
    events: AsyncIterator[dict[str, Any]],
    *,
    deadline_seconds: float = TURN_DEADLINE_SECONDS,
) -> AsyncIterator[str]:
    iterator = events.__aiter__()
    pending: asyncio.Task[dict[str, Any]] | None = None
    deadline = time.monotonic() + deadline_seconds
    answered = False
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("chat turn passed its %.0fs deadline", deadline_seconds)
                if not answered:
                    yield _sse({"type": "error", "message": TOO_SLOW_MESSAGE})
                return
            done, _ = await asyncio.wait(
                {pending}, timeout=min(KEEPALIVE_SECONDS, remaining)
            )
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                if not answered:
                    log.warning("chat stream ended without an answer")
                    yield _sse({"type": "error", "message": STOPPED_MESSAGE})
                return
            pending = None
            answered = answered or event.get("type") in _TERMINAL_EVENTS
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
    if isinstance(invoker, AgentCoreRuntimeInvoker):
        raise ConflictError(
            "Synchronous chat is retired. Submit a background question through "
            "/api/v1/chat/jobs and poll its saved progress."
        )
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
    try:
        with ctx.get_chat_jobs().deleting_actor(session.actor_id):
            deleted = await ctx.get_agent_invoker().delete_memory(
                actor_id=session.actor_id
            )
    except JobConflictError as error:
        raise ConflictError(str(error)) from error
    return {"deleted_events": deleted}
