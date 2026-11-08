"""Tests the analytics entrypoint's request handling (Task 11.5/19.2).

Module-scope client construction is lazy (no AWS call until used), so the
module imports in ordinary CI once ``XC_SNAPSHOT_BUCKET`` is set. With no
``XC_MEMORY_ID`` the entrypoint uses the in-memory conversation store. The
chat path's model call is covered by agents/test_chat_stream.py against a
scripted model; here only validation and memory deletion are exercised.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ.setdefault("XC_SNAPSHOT_BUCKET", "test-bucket")
os.environ.setdefault("XC_CHAT_JOB_BUCKET", "test-job-bucket")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.pop("XC_MEMORY_ID", None)

from xc_platform.agents.agentcore_entrypoints import analytics_entrypoint

ACTOR = "a" * 64


def _collect(payload: dict[str, Any]) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        return [e async for e in analytics_entrypoint.invoke(payload, object())]

    return asyncio.run(run())


def test_module_imports_without_a_real_aws_call() -> None:
    assert analytics_entrypoint.app is not None


def test_chat_rejects_a_raw_subject_as_actor_id() -> None:
    events = _collect(
        {"actor_id": "coach@example.com", "session_id": "session-1", "prompt": "hi"}
    )
    assert events == [
        {"type": "error", "message": "actor_id must be a 64-character hex digest"}
    ]


def test_chat_rejects_a_missing_prompt() -> None:
    events = _collect({"actor_id": ACTOR, "session_id": "session-1", "prompt": " "})
    assert events[0]["type"] == "error"
    assert "prompt" in events[0]["message"]


def test_chat_rejects_a_malformed_session_id() -> None:
    events = _collect({"actor_id": ACTOR, "session_id": "../x", "prompt": "hi"})
    assert events[0]["type"] == "error"


def test_delete_memory_removes_only_that_actors_turns() -> None:
    store = analytics_entrypoint._store
    other = "b" * 64
    store.append(ACTOR, "session-1", "q1", "a1")
    store.append(ACTOR, "session-2", "q2", "a2")
    store.append(other, "session-1", "q3", "a3")

    events = _collect({"action": "delete_memory", "actor_id": ACTOR})

    assert events == [{"type": "memory_deleted", "events": 2}]
    assert store.load(ACTOR, "session-1") == []
    assert len(store.load(other, "session-1")) == 2


def test_background_start_acknowledges_without_waiting_for_agent(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    import uuid

    from xc_platform.agents.chat_jobs import ChatJobStore
    from xc_platform.db.publication.s3_client import FakeS3Client

    jobs = ChatJobStore(FakeS3Client(), work_dir=tmp_path)
    job_id = str(uuid.uuid4())
    jobs.submit(ACTOR, "session-1", job_id, "hello")
    monkeypatch.setattr(analytics_entrypoint, "_jobs", jobs)
    calls = []

    def worker(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(analytics_entrypoint, "start_worker", worker)
    events = _collect(
        {
            "action": "start_job",
            "actor_id": ACTOR,
            "session_id": "session-1",
            "job_id": job_id,
            "prompt": "hello",
        }
    )
    assert events == [{"type": "job_accepted", "job_id": job_id}]
    assert len(calls) == 1
    assert callable(calls[0][1]["register"])
    assert calls[0][1]["complete"] == analytics_entrypoint.app.complete_async_task


def test_background_start_refuses_another_actors_job(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    import uuid

    from xc_platform.agents.chat_jobs import ChatJobStore
    from xc_platform.db.publication.s3_client import FakeS3Client

    jobs = ChatJobStore(FakeS3Client(), work_dir=tmp_path)
    job_id = str(uuid.uuid4())
    jobs.submit("b" * 64, "session-1", job_id, "hello")
    monkeypatch.setattr(analytics_entrypoint, "_jobs", jobs)
    events = _collect(
        {
            "action": "start_job",
            "actor_id": ACTOR,
            "session_id": "session-1",
            "job_id": job_id,
            "prompt": "hello",
        }
    )
    assert events == [{"type": "error", "message": "chat job not found"}]
