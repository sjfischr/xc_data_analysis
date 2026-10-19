"""One streamed analytics turn as a sequence of UI events (Task 19.2).

This is the chat contract every runtime speaks -- the AgentCore entrypoint
yields these events from its streaming handler, and the local/in-process
invoker yields them directly -- so the API's SSE relay and the browser
never care where the agent ran.

Event types (all JSON objects with a ``type``):

- ``status``      ``{status: "thinking", publication_id}`` -- first event
- ``text``        ``{delta}`` -- streamed model text as it is generated
- ``tool``        ``{id, name, label, status: "running"|"done"|"error"}``
- ``chart``       ``{title, spec}`` -- a Vega-Lite spec (agents/chart_spec.py)
- ``image``       ``{title, src}`` -- a PNG data URI from the Python sandbox
- ``code``        ``{language, code, sql, stdout, stderr, ok}``
- ``follow_ups``  ``{questions}``
- ``context``     ``{notes}`` -- working notes for the next turn's history
                  (``turn_notes``); internal, never relayed to the browser
                  (agents/chat_service.py consumes it)
- ``final``       ``{markdown}`` -- the sanitized final answer; replaces the
                  streamed text, which can include pre-tool narration
- ``complete``    ``{stop_reason, usage, publication_id, model_id}`` --
                  ``stop_reason`` is ``limit_time`` when the turn ran out of
                  wall-clock time (``TURN_DEADLINE_SECONDS``)
- ``error``       ``{message}``
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from strands.types.agent import Limits
from strands.types.content import Messages
from strands.types.exceptions import MaxTokensReachedException

from xc_platform.agents.analytics_agent import (
    DEFAULT_LIMITS,
    build_analytics_agent,
)
from xc_platform.agents.code_interpreter import PythonExecutor
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.security.markdown_sanitizer import sanitize_markdown

log = logging.getLogger(__name__)

TOOL_LABELS = {
    "list_dimensions_tool": "Checking what data is available",
    "find_athletes_tool": "Looking up athletes",
    "find_schools_tool": "Looking up schools",
    "get_athlete_profile_tool": "Reading athlete history",
    "compare_athletes_tool": "Comparing athletes",
    "project_standing_tool": "Projecting standings",
    "get_leaderboards_tool": "Pulling leaderboards",
    "list_results_tool": "Reading race results",
    "get_team_scores_tool": "Reading team scores",
    "get_saint_sebastian_standings_tool": "Reading Saint Sebastian standings",
    "team_score_what_if_tool": "Running a what-if scenario",
    "calculate_tool": "Calculating",
    "run_readonly_query_tool": "Querying the database",
    "build_chart_tool": "Drawing a chart",
    "suggest_follow_ups_tool": "Suggesting follow-ups",
    "run_python_analysis_tool": "Running Python analysis",
}

# How much of the conversation seeds the next turn. Earlier turns arrive
# from the conversation store as question + answer + working notes (the
# tools, arguments, and ids the answer used -- ``turn_notes``), so a
# follow-up like "and her best race?" can reuse the exact athlete_id.
MAX_HISTORY_MESSAGES = 24

# Tools whose calls say nothing a follow-up needs.
_NOTELESS_TOOLS = frozenset({"suggest_follow_ups_tool", "calculate_tool"})
# Tools whose results map names to ids -- the mapping a follow-up reuses.
_ID_FIELDS = (("athlete_id", "display_name"), ("school_id", "display_name"))
MAX_NOTE_MATCHES = 8
MAX_NOTE_ARG_CHARS = 300

LIMIT_NOTE = (
    "*I ran out of steps before finishing this one. Try a narrower question "
    "-- one season, division, school, or athlete -- or ask me to continue.*"
)

TIME_LIMIT_NOTE = (
    "*This one was taking too long, so I stopped. Try a narrower question "
    "-- one season, division, school, or athlete -- or ask for one piece "
    "at a time.*"
)

# A turn's wall-clock budget. App Runner ends every request at 120 seconds,
# so an answer still being worked on after that can never reach the browser
# -- and while it runs, it holds the conversation's AgentCore session. Stop
# in time to say so instead.
TURN_DEADLINE_SECONDS = 100.0

_END = object()


@dataclass(frozen=True)
class _Failure:
    error: BaseException


# Tools that only present; text beside a call to one of them is part of the
# answer, while text beside a data-tool call is pre-tool narration.
PRESENTATION_TOOLS = frozenset({"build_chart_tool", "suggest_follow_ups_tool"})


def answer_text(messages: Messages) -> str:
    """The answer for one turn, from the assistant messages it produced.

    Strands' ``str(result)`` is only the last message's text, but the model
    often writes its answer in the same message as its final
    ``suggest_follow_ups_tool``/``build_chart_tool`` call, leaving the last
    message empty. Narration that accompanies a data-tool call ("Let me look
    that up") is left out.
    """
    parts: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        blocks = message.get("content", [])
        tool_names = {b["toolUse"].get("name") for b in blocks if "toolUse" in b}
        if tool_names - PRESENTATION_TOOLS:
            continue
        for block in blocks:
            text = block.get("text", "").strip()
            if text and tool_names:
                text = _strip_narration(text)
            if text:
                parts.append(text)
    return "\n\n".join(parts)


_NARRATION = re.compile(
    r"^(?:(?:perfect|great|excellent|okay|ok|got it)[!.,]\s*)?"
    r"(?:now\s+)?(?:let me|i'll|i will|i'm going to)\b[^\n.!?]*"
    r"\b(?:chart|graph|visuali[sz]\w*|plot|look|check|pull|fetch|get|find|search"
    r"|query|calculat\w*|comput\w*|create|draw|show|suggest)\b[^\n.!?]*[.!?:]\s*",
    re.IGNORECASE,
)


def _strip_narration(text: str) -> str:
    """Drop a leading "Perfect! Now I'll create a chart:" style lead-in the
    model sometimes writes beside a presentation-tool call, despite the
    system prompt asking it not to narrate."""
    return _NARRATION.sub("", text, count=1).strip()


def _format_args(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    parts = []
    for key, value in tool_input.items():
        if key == "code" or value in (None, [], ""):
            continue  # model-written Python is noise for a follow-up
        if key == "data":
            parts.append(
                f"data=<{len(value) if isinstance(value, list) else '?'} rows>"
            )
            continue
        text = json.dumps(value, ensure_ascii=False)
        if len(text) > MAX_NOTE_ARG_CHARS:
            text = text[:MAX_NOTE_ARG_CHARS] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _resolved_ids(result_text: str) -> list[str]:
    """``Name [id]`` pairs from a find/profile/compare tool result."""
    try:
        data = json.loads(result_text)
    except (TypeError, ValueError):
        return []
    found: list[str] = []

    def visit(obj: Any) -> None:
        if len(found) >= MAX_NOTE_MATCHES:
            return
        if isinstance(obj, dict):
            for id_key, name_key in _ID_FIELDS:
                if id_key in obj and name_key in obj:
                    found.append(f"{obj[name_key]} [{id_key}={obj[id_key]}]")
                    return
            for value in obj.values():
                visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(data)
    return found


def turn_notes(messages: Messages) -> str:
    """Working notes for one turn: each data tool called, its arguments,
    and the names it resolved to ids. Stored with the turn so the next
    question in the conversation can reuse them (conversation_memory.py)."""
    calls: dict[str, tuple[str, str]] = {}
    order: list[str] = []
    results: dict[str, str] = {}
    for message in messages:
        for block in message.get("content", []):
            use = block.get("toolUse")
            if use and use.get("name") not in _NOTELESS_TOOLS:
                calls[use["toolUseId"]] = (use["name"], _format_args(use.get("input")))
                order.append(use["toolUseId"])
            outcome = block.get("toolResult")
            if outcome:
                texts = [c.get("text", "") for c in outcome.get("content", [])]
                results[outcome.get("toolUseId", "")] = "".join(texts)
    lines = []
    for tool_id in order:
        name, args = calls[tool_id]
        line = f"- {name}({args})"
        resolved = _resolved_ids(results.get(tool_id, ""))
        if resolved:
            line += " -> " + "; ".join(resolved)
        lines.append(line)
    return "\n".join(lines)


def _trim_history(messages: Messages) -> Messages:
    """Keep the last few complete exchanges. A cut must never start on a
    tool result (its tool call would be missing), so it starts at a user
    message that carries text."""
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return list(messages)
    tail = list(messages[-MAX_HISTORY_MESSAGES:])
    for i, message in enumerate(tail):
        if message.get("role") == "user" and any(
            "text" in block for block in message.get("content", [])
        ):
            return tail[i:]
    return []


async def stream_turn(
    *,
    snapshot_reader: SnapshotReader,
    prompt: str,
    history: Messages | None = None,
    model: Any | None = None,
    model_id: str | None = None,
    python_executor: PythonExecutor | None = None,
    limits: Limits | None = None,
    on_complete: Callable[[Messages], None] | None = None,
    deadline_seconds: float = TURN_DEADLINE_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    started = time.monotonic()
    pinned = snapshot_reader.current()
    publication_id = pinned.manifest.publication_id
    connection = pinned.open_connection()
    ui_events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()

    def drain() -> list[dict[str, Any]]:
        drained = []
        while True:
            try:
                drained.append(ui_events.get_nowait())
            except queue.Empty:
                return drained

    pump: asyncio.Task[None] | None = None
    try:
        agent = build_analytics_agent(
            CanonicalReadRepository(connection),
            publication_id=publication_id,
            model=model,
            model_id=model_id,
            emit=ui_events.put,
            python_executor=python_executor,
            history=_trim_history(history or []),
        )
        turn_start = len(agent.messages)
        yield {"type": "status", "status": "thinking", "publication_id": publication_id}

        # The agent runs in its own task, so the turn can stop waiting for it
        # at the deadline; the queue carries its events (or its exception).
        agent_events: asyncio.Queue[Any] = asyncio.Queue()

        async def run_agent() -> None:
            try:
                async for agent_event in agent.stream_async(
                    prompt, limits=limits or DEFAULT_LIMITS
                ):
                    agent_events.put_nowait(agent_event)
            except Exception as error:
                agent_events.put_nowait(_Failure(error))
            finally:
                agent_events.put_nowait(_END)

        pump = asyncio.create_task(run_agent())
        deadline = started + deadline_seconds
        timed_out = False

        announced: set[str] = set()
        result: Any = None
        while True:
            try:
                event = await asyncio.wait_for(
                    agent_events.get(), max(deadline - time.monotonic(), 0.0)
                )
            except TimeoutError:
                timed_out = True
                break
            if event is _END:
                break
            if isinstance(event, _Failure):
                raise event.error
            for ui_event in drain():
                yield ui_event
            if "data" in event and isinstance(event["data"], str):
                yield {"type": "text", "delta": event["data"]}
            elif event.get("type") == "tool_use_stream":
                current = event.get("current_tool_use") or {}
                tool_id = str(current.get("toolUseId", ""))
                if tool_id and tool_id not in announced:
                    announced.add(tool_id)
                    name = str(current.get("name", ""))
                    yield {
                        "type": "tool",
                        "id": tool_id,
                        "name": name,
                        "label": TOOL_LABELS.get(name, name),
                        "status": "running",
                    }
            elif "message" in event:
                message = event["message"]
                for block in message.get("content", []):
                    tool_result = block.get("toolResult")
                    if tool_result:
                        yield {
                            "type": "tool",
                            "id": tool_result.get("toolUseId"),
                            "status": "error"
                            if tool_result.get("status") == "error"
                            else "done",
                        }
                    tool_use = block.get("toolUse")
                    if tool_use and tool_use.get("toolUseId") not in announced:
                        announced.add(tool_use["toolUseId"])
                        name = tool_use.get("name", "")
                        yield {
                            "type": "tool",
                            "id": tool_use["toolUseId"],
                            "name": name,
                            "label": TOOL_LABELS.get(name, name),
                            "status": "running",
                        }
            elif "result" in event:
                result = event["result"]
        for ui_event in drain():
            yield ui_event

        if timed_out:
            log.warning(
                "analytics turn stopped at its %.0fs deadline", deadline_seconds
            )
            yield {"type": "final", "markdown": TIME_LIMIT_NOTE}
            yield {"type": "context", "notes": turn_notes(agent.messages[turn_start:])}
            yield {
                "type": "complete",
                "stop_reason": "limit_time",
                "publication_id": publication_id,
                "model_id": getattr(agent.model, "config", {}).get("model_id"),
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "cycles": 0,
                },
            }
            return
        if result is None:
            yield {"type": "error", "message": "the agent returned no result"}
            return
        answer = answer_text(agent.messages[turn_start:]) or str(result).strip()
        if not answer or str(result.stop_reason).startswith("limit"):
            answer = f"{answer}\n\n{LIMIT_NOTE}" if answer else LIMIT_NOTE
        yield {"type": "final", "markdown": sanitize_markdown(answer)}
        yield {"type": "context", "notes": turn_notes(agent.messages[turn_start:])}
        usage = result.metrics.accumulated_usage
        resolved_model_id = getattr(agent.model, "config", {}).get("model_id")
        yield {
            "type": "complete",
            "stop_reason": str(result.stop_reason),
            "publication_id": publication_id,
            "model_id": resolved_model_id,
            "usage": {
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
                "cache_read_tokens": usage.get("cacheReadInputTokens", 0),
                "cache_write_tokens": usage.get("cacheWriteInputTokens", 0),
                "cycles": result.metrics.cycle_count,
            },
        }
        if on_complete is not None:
            on_complete(_trim_history(agent.messages))
    except MaxTokensReachedException:
        # One model response overflowed max_tokens (e.g. it tried to write a
        # huge literal). Report it as a limit, not a crash.
        log.warning("analytics turn hit max_tokens")
        for ui_event in drain():
            yield ui_event
        yield {"type": "final", "markdown": LIMIT_NOTE}
        yield {
            "type": "complete",
            "stop_reason": "limit_max_tokens",
            "publication_id": publication_id,
            "model_id": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cycles": 0,
            },
        }
    except Exception as error:
        log.exception("analytics turn failed")
        yield {"type": "error", "message": _public_error(error)}
    finally:
        if pump is not None and not pump.done():
            # A tool already running on a worker thread finishes on its own;
            # the agent loop stops waiting for it.
            pump.cancel()
            with contextlib.suppress(BaseException):
                await pump
        if python_executor is not None:
            python_executor.close()
        connection.close()


def _public_error(error: Exception) -> str:
    name = type(error).__name__
    text = str(error)
    if "ThrottlingException" in text or name == "ModelThrottledException":
        return "The model is busy right now. Please try again in a moment."
    if "AccessDenied" in text:
        return "The analytics model is not available to this deployment."
    return "Something went wrong while answering. Please try again."
