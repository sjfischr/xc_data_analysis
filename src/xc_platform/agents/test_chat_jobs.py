from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from xc_platform.agents.chat_jobs import (
    ChatJobStore,
    JobConflictError,
    JobNotFoundError,
    execute_job,
    start_worker,
)
from xc_platform.db.publication.s3_client import FakeS3Client

ACTOR = "a" * 64
OTHER = "b" * 64
SESSION = "conversation-1"


@pytest.fixture
def store(tmp_path: Path) -> ChatJobStore:
    return ChatJobStore(FakeS3Client(), work_dir=tmp_path)


def submit(store: ChatJobStore) -> str:
    job_id = str(uuid.uuid4())
    assert store.submit(ACTOR, SESSION, job_id, "Question").created
    return job_id


def test_idempotency_is_question_and_actor_scoped(store: ChatJobStore) -> None:
    job_id = submit(store)
    assert not store.submit(ACTOR, SESSION, job_id, "Question").created
    with pytest.raises(JobConflictError):
        store.submit(ACTOR, SESSION, job_id, "Different")
    with pytest.raises(JobNotFoundError):
        store.get(OTHER, job_id)
    assert store.submit(OTHER, SESSION, job_id, "Other question").created


def test_parallel_submissions_only_accept_one_question(store: ChatJobStore) -> None:
    def attempt(_: int) -> bool:
        try:
            submit(store)
            return True
        except JobConflictError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 1
    assert len(store.list_jobs(ACTOR, SESSION)) == 1


def test_worker_claim_is_create_once_and_terminal_jobs_are_not_restarted(
    store: ChatJobStore,
) -> None:
    job_id = submit(store)
    assert store.claim(ACTOR, job_id)
    assert not store.claim(ACTOR, job_id)
    store.finish(ACTOR, job_id, "completed")
    assert not store.claim(ACTOR, job_id)
    assert not store.submit(ACTOR, SESSION, job_id, "Question").created
    submit(store)


def test_event_replay_and_restart_keep_charts_and_cursor(store: ChatJobStore) -> None:
    job_id = submit(store)
    store.claim(ACTOR, job_id)
    events = [{"type": "text", "delta": str(i)} for i in range(125)]
    store.append(ACTOR, job_id, events[:100])
    store.append(ACTOR, job_id, events[100:])
    store.append(
        ACTOR,
        job_id,
        [{"type": "chart", "spec": {"data": {"values": [{"x": 1}]}}, "title": "Chart"}],
    )
    store.finish(ACTOR, job_id, "completed")
    restarted = ChatJobStore(store.s3, work_dir=store.work_dir)
    page = restarted.poll(ACTOR, job_id, 0)
    assert page["events"] == events[:100]
    assert page["has_more"] and page["next_cursor"] == 100
    page = restarted.poll(ACTOR, job_id, 100)
    assert page["events"][:25] == events[100:]
    assert page["events"][-1]["type"] == "chart"
    assert not page["has_more"] and page["next_cursor"] == 126
    assert restarted.poll(ACTOR, job_id, 126)["events"] == []
    with pytest.raises(ValueError, match="cursor"):
        restarted.poll(ACTOR, job_id, 127)


def test_dead_worker_is_explicitly_failed_not_forever_busy(store: ChatJobStore) -> None:
    job_id = submit(store)
    store.claim(ACTOR, job_id)
    key = store._job_key(ACTOR, job_id)
    record, etag = store._read(key)
    record["heartbeat"] = time.time() - 100
    store._write(key, record, etag=etag)
    record = store.get(ACTOR, job_id)
    assert record["state"] == "failed"
    assert "stopped responding" in record["error"]
    submit(store)


def test_cancel_watches_idle_agent_and_closes_generator(store: ChatJobStore) -> None:
    job_id = submit(store)
    store.claim(ACTOR, job_id)
    closed = []

    async def source() -> Any:
        try:
            yield {"type": "status", "publication_id": "publication-1"}
            await asyncio.sleep(10)
            yield {"type": "final", "markdown": "Too late"}
        finally:
            closed.append(True)

    async def run() -> None:
        task = asyncio.create_task(execute_job(store, ACTOR, job_id, source()))
        await asyncio.sleep(0.01)
        store.cancel(ACTOR, job_id)
        await asyncio.wait_for(task, 3)

    asyncio.run(run())
    assert closed == [True]
    record = store.get(ACTOR, job_id)
    assert record["state"] == "cancelled"
    assert record["publication_id"] == "publication-1"
    assert not any(e["type"] == "final" for e in store.poll(ACTOR, job_id, 0)["events"])


def test_completed_job_and_registered_health_lifecycle(store: ChatJobStore) -> None:
    job_id = submit(store)
    lifecycle: list[str | int] = []

    async def source() -> Any:
        yield {"type": "final", "markdown": "Answer"}
        yield {"type": "complete", "stop_reason": "end_turn"}

    def register() -> int:
        lifecycle.append("register")
        return 7

    start_worker(
        store,
        ACTOR,
        job_id,
        source,
        register=register,
        complete=lambda task_id: lifecycle.append(task_id),
    )
    for _ in range(100):
        if store.get(ACTOR, job_id)["state"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert store.get(ACTOR, job_id)["state"] == "completed"
    assert lifecycle == ["register", 7]
    assert store.poll(ACTOR, job_id, 0)["events"][0]["markdown"] == "Answer"


def test_delete_history_removes_every_actor_job_and_not_other_users(
    store: ChatJobStore,
) -> None:
    job_id = submit(store)
    other_id = str(uuid.uuid4())
    store.submit(OTHER, SESSION, other_id, "Other")
    with pytest.raises(JobConflictError, match="Cancel"):
        store.delete_actor(ACTOR)
    store.claim(ACTOR, job_id)
    store.append(ACTOR, job_id, [{"type": "final", "markdown": "Answer"}])
    store.finish(ACTOR, job_id, "completed")
    assert store.delete_actor(ACTOR) == 1
    assert store.list_jobs(ACTOR, SESSION) == []
    with pytest.raises(JobNotFoundError):
        store.get(ACTOR, job_id)
    assert store.get(OTHER, other_id)["state"] == "pending"


def test_incomplete_agent_stream_is_failed(store: ChatJobStore) -> None:
    job_id = submit(store)
    store.claim(ACTOR, job_id)

    async def source() -> Any:
        yield {"type": "text", "delta": "unfinished"}

    asyncio.run(execute_job(store, ACTOR, job_id, source()))
    assert store.get(ACTOR, job_id)["state"] == "failed"


def test_memory_deletion_keeps_submission_excluded(store: ChatJobStore) -> None:
    job_id = submit(store)
    store.finish(ACTOR, job_id, "completed")
    with store.deleting_actor(ACTOR) as deleted:
        assert deleted == 1
        with pytest.raises(JobConflictError, match="history operation"):
            submit(store)
    submit(store)


def test_long_history_operation_renews_its_lease(
    store: ChatJobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [time.time()]
    monkeypatch.setattr("xc_platform.agents.chat_jobs.time.time", lambda: now[0])
    with store._actor_operation(ACTOR) as renew:
        now[0] += 100
        renew()
        now[0] += 220
        with pytest.raises(JobConflictError, match="history operation"):
            submit(store)
        renew()
    submit(store)


def test_invalid_identifiers_never_address_another_s3_prefix(
    store: ChatJobStore,
) -> None:
    with pytest.raises(ValueError, match="actor"):
        store.get("../../other", str(uuid.uuid4()))
    with pytest.raises(ValueError, match="UUID"):
        store.get(ACTOR, "../other")
    with pytest.raises(ValueError, match="session"):
        store.list_jobs(ACTOR, "../other")
