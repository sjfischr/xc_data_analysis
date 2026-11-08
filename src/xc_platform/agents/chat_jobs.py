"""Durable, actor-scoped chat jobs independent of HTTP request lifetimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xc_platform.db.publication.errors import (
    ObjectNotFoundError,
    PreconditionFailedError,
)
from xc_platform.db.publication.s3_client import S3Client

log = logging.getLogger(__name__)
JOB_SECONDS = 28800 - 60
HEARTBEAT_SECONDS = 5.0
STALE_SECONDS = 90.0
MAX_JOBS = 50
TERMINAL = frozenset({"completed", "failed", "cancelled"})
_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
_ACTOR = re.compile(r"^[0-9a-f]{64}$")


class JobNotFoundError(RuntimeError):
    pass


class JobConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobSubmission:
    job: dict[str, Any]
    created: bool


class ChatJobStore:
    """S3 conditional writes arbitrate retries and conversation concurrency."""

    def __init__(self, s3: S3Client, *, work_dir: Path) -> None:
        self.s3 = s3
        self.work_dir = work_dir
        work_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, actor: str, suffix: str) -> str:
        if not _ACTOR.fullmatch(actor):
            raise ValueError("invalid actor_id")
        return f"chat-jobs/{actor}/{suffix}"

    def _job_key(self, actor: str, job_id: str, suffix: str = "status.json") -> str:
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError
        except ValueError as error:
            raise ValueError("job_id must be a canonical UUID") from error
        return self._key(actor, f"jobs/{job_id}/{suffix}")

    def _session_key(self, actor: str, session: str) -> str:
        if not _ID.fullmatch(session):
            raise ValueError("invalid session_id")
        return self._key(actor, f"sessions/{session}.json")

    def _read(self, key: str) -> tuple[dict[str, Any], str]:
        path = self.work_dir / f"{uuid.uuid4().hex}.json"
        try:
            meta = self.s3.get_object(key, path)
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"invalid chat job object {key}")
            return value, meta.etag
        finally:
            path.unlink(missing_ok=True)

    def _write(
        self, key: str, value: dict[str, Any], *, etag: str | None = None
    ) -> None:
        path = self.work_dir / f"{uuid.uuid4().hex}.json"
        try:
            path.write_text(json.dumps(value), encoding="utf-8")
            self.s3.put_object(key, path, if_none_match=etag is None, if_match=etag)
        finally:
            path.unlink(missing_ok=True)

    def get(self, actor: str, job_id: str) -> dict[str, Any]:
        try:
            record, _ = self._read(self._job_key(actor, job_id))
        except ObjectNotFoundError as error:
            raise JobNotFoundError("chat job not found") from error
        if record["state"] not in TERMINAL and (
            time.time() - record["heartbeat"] > STALE_SECONDS
            or time.time() > record["expires_at"]
        ):
            self.finish(
                actor,
                job_id,
                "failed",
                "The background worker stopped responding. Its saved progress "
                "is available below; submit a new question to try again.",
            )
            record, _ = self._read(self._job_key(actor, job_id))
        return record

    @staticmethod
    def public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record[key]
            for key in (
                "job_id",
                "session_id",
                "prompt",
                "state",
                "created_at",
                "updated_at",
                "publication_id",
                "error",
                "next_cursor",
            )
        }

    def submit(
        self, actor: str, session: str, job_id: str, prompt: str
    ) -> JobSubmission:
        with self._actor_operation(actor):
            return self._submit(actor, session, job_id, prompt)

    @contextmanager
    def _actor_operation(self, actor: str) -> Iterator[Callable[[], None]]:
        key = self._key(actor, "operation.json")
        expires_at = time.time() + 300
        value = {"expires_at": expires_at, "owner": uuid.uuid4().hex}
        try:
            self._write(key, value)
        except PreconditionFailedError:
            previous, etag = self._read(key)
            if previous["expires_at"] > time.time():
                raise JobConflictError(
                    "Another history operation is in progress. Please retry."
                ) from None
            try:
                self._write(key, value, etag=etag)
            except PreconditionFailedError as error:
                raise JobConflictError(
                    "Concurrent history operation; retry."
                ) from error
        _, etag = self._read(key)

        def renew() -> None:
            nonlocal etag, expires_at
            if expires_at - time.time() > 240:
                return
            expires_at = time.time() + 300
            value["expires_at"] = expires_at
            try:
                self._write(key, value, etag=etag)
            except PreconditionFailedError as error:
                raise JobConflictError(
                    "History operation lease was lost; retry."
                ) from error
            _, etag = self._read(key)

        try:
            yield renew
        finally:
            try:
                self.s3.delete_object(key, if_match=etag)
            except PreconditionFailedError:
                log.warning(
                    "chat history operation lease was replaced actor=%s", actor[:8]
                )

    def _submit(
        self, actor: str, session: str, job_id: str, prompt: str
    ) -> JobSubmission:
        from xc_platform.agents.chat_service import validate_chat_request

        _, _, prompt = validate_chat_request(actor, session, prompt)
        job_key = self._job_key(actor, job_id)
        session_key = self._session_key(actor, session)
        self._remember_session(actor, session)
        try:
            existing = self.get(actor, job_id)
        except JobNotFoundError:
            existing = None
        if existing is not None:
            if existing["session_id"] != session or existing["prompt"] != prompt:
                raise JobConflictError("job_id already belongs to a different question")
            return JobSubmission(existing, False)

        # Index first: a crash at any following point still leaves the
        # submission discoverable after reconnect. The lock is CAS-protected.
        for _ in range(10):
            try:
                index, etag = self._read(session_key)
            except ObjectNotFoundError:
                index, etag = {"jobs": []}, None
            ids = index["jobs"]
            if len(ids) >= MAX_JOBS and job_id not in ids:
                raise JobConflictError("This conversation is full. Start a new one.")
            if ids:
                try:
                    previous = self.get(actor, ids[-1])
                except JobNotFoundError:
                    previous = None
                    if ids[-1] != job_id and (
                        time.time() - index.get("reserved_at", 0) < STALE_SECONDS
                    ):
                        raise JobConflictError(
                            "A submission is being accepted; retry the same job ID."
                        ) from None
                if previous is not None and previous["state"] not in TERMINAL:
                    raise JobConflictError(
                        "This conversation already has a question running."
                    )
            index["jobs"] = [*ids] if job_id in ids else [*ids, job_id]
            index["reserved_at"] = time.time()
            try:
                self._write(session_key, index, etag=etag)
                break
            except PreconditionFailedError:
                continue
        else:
            raise JobConflictError("Concurrent submissions; please retry.")
        now = time.time()
        record = {
            "job_id": job_id,
            "session_id": session,
            "prompt": prompt,
            "state": "pending",
            "created_at": now,
            "updated_at": now,
            "heartbeat": now,
            "expires_at": now + JOB_SECONDS,
            "publication_id": None,
            "error": None,
            "next_cursor": 0,
            "segments": [],
            "claimed": False,
        }
        try:
            self._write(job_key, record)
        except PreconditionFailedError:
            existing = self.get(actor, job_id)
            if existing["prompt"] != prompt or existing["session_id"] != session:
                raise JobConflictError(
                    "job_id already belongs to another question"
                ) from None
            return JobSubmission(existing, False)
        return JobSubmission(record, True)

    def _remember_session(self, actor: str, session: str) -> None:
        key = self._key(actor, "sessions.json")
        for _ in range(10):
            try:
                index, etag = self._read(key)
            except ObjectNotFoundError:
                index, etag = {"sessions": []}, None
            if session not in index["sessions"]:
                if len(index["sessions"]) >= 200:
                    raise JobConflictError(
                        "Saved conversation limit reached. Delete history before "
                        "starting another conversation."
                    )
                index["sessions"].append(session)
            try:
                self._write(key, index, etag=etag)
                return
            except PreconditionFailedError:
                continue
        raise JobConflictError("Could not record conversation index; retry.")

    def list_jobs(self, actor: str, session: str) -> list[dict[str, Any]]:
        try:
            index, _ = self._read(self._session_key(actor, session))
        except ObjectNotFoundError:
            return []
        records = []
        for job_id in index["jobs"]:
            try:
                records.append(self.get(actor, job_id))
            except JobNotFoundError:
                log.warning(
                    "incomplete chat submission job=%s actor=%s", job_id, actor[:8]
                )
        return records

    def claim(self, actor: str, job_id: str) -> bool:
        key = self._job_key(actor, job_id)
        record, etag = self._read(key)
        if record["claimed"] or record["state"] in TERMINAL:
            return False
        record.update(claimed=True, state="running", heartbeat=time.time())
        try:
            self._write(key, record, etag=etag)
        except PreconditionFailedError:
            return False
        return True

    def append(self, actor: str, job_id: str, events: list[dict[str, Any]]) -> None:
        key = self._job_key(actor, job_id)
        record, etag = self._read(key)
        if record["state"] in TERMINAL:
            return
        if record["next_cursor"] + len(events) > 10000:
            raise RuntimeError("chat job exceeded its saved event limit")
        if events:
            start = record["next_cursor"]
            segment_key = self._job_key(actor, job_id, f"events/{start:08d}.json")
            self._write(segment_key, {"events": events})
            record["segments"].append({"start": start, "count": len(events)})
            record["next_cursor"] += len(events)
            for event in events:
                if event.get("publication_id"):
                    record["publication_id"] = event["publication_id"]
        record.update(heartbeat=time.time(), updated_at=time.time())
        self._write(key, record, etag=etag)

    def poll(self, actor: str, job_id: str, after: int) -> dict[str, Any]:
        record = self.get(actor, job_id)
        if after < 0 or after > record["next_cursor"]:
            raise ValueError("invalid event cursor")
        events: list[dict[str, Any]] = []
        for segment in record["segments"]:
            if segment["start"] + segment["count"] <= after:
                continue
            value, _ = self._read(
                self._job_key(actor, job_id, f"events/{segment['start']:08d}.json")
            )
            skip = max(0, after - segment["start"])
            events.extend(value["events"][skip:][: 100 - len(events)])
            if len(events) >= 100:
                break
        cursor = after + len(events)
        return {
            "job": self.public(record),
            "events": events,
            "next_cursor": cursor,
            "has_more": cursor < record["next_cursor"],
        }

    def cancelled(self, actor: str, job_id: str) -> bool:
        return (
            self.s3.head_object(self._job_key(actor, job_id, "cancel.json")) is not None
        )

    def cancel(self, actor: str, job_id: str) -> dict[str, Any]:
        record = self.get(actor, job_id)
        if record["state"] not in TERMINAL:
            try:
                self._write(self._job_key(actor, job_id, "cancel.json"), {})
            except PreconditionFailedError:
                pass  # Already requested, not a second cancellation.
        return self.public(record)

    def finish(
        self, actor: str, job_id: str, state: str, error: str | None = None
    ) -> None:
        key = self._job_key(actor, job_id)
        for _ in range(10):
            record, etag = self._read(key)
            if record["state"] in TERMINAL:
                return
            record.update(state=state, error=error, updated_at=time.time())
            try:
                self._write(key, record, etag=etag)
                return
            except PreconditionFailedError:
                continue
        raise JobConflictError("Could not finish job due to concurrent updates")

    def delete_actor(self, actor: str) -> int:
        with self.deleting_actor(actor) as deleted:
            return deleted

    @contextmanager
    def deleting_actor(self, actor: str) -> Iterator[int]:
        """Keep submissions excluded until the caller also erases Memory."""
        with self._actor_operation(actor) as renew:
            deleted = self._delete_actor(actor, renew)
            renew()
            yield deleted

    def _delete_actor(self, actor: str, renew: Callable[[], None]) -> int:
        try:
            index, _ = self._read(self._key(actor, "sessions.json"))
        except ObjectNotFoundError:
            return 0
        jobs = []
        for session in index["sessions"]:
            renew()
            jobs.extend(self.list_jobs(actor, session))
        if any(record["state"] not in TERMINAL for record in jobs):
            raise JobConflictError("Cancel active questions before deleting history.")
        for record in jobs:
            job_id = record["job_id"]
            for segment in record["segments"]:
                renew()
                self.s3.delete_object(
                    self._job_key(actor, job_id, f"events/{segment['start']:08d}.json")
                )
            renew()
            self.s3.delete_object(self._job_key(actor, job_id, "cancel.json"))
            self.s3.delete_object(self._job_key(actor, job_id))
        for session in index["sessions"]:
            renew()
            self.s3.delete_object(self._session_key(actor, session))
        self.s3.delete_object(self._key(actor, "sessions.json"))
        return len(jobs)


async def execute_job(
    store: ChatJobStore,
    actor: str,
    job_id: str,
    events: AsyncIterator[dict[str, Any]],
) -> None:
    """Journal output and watch cancellation without binding to an HTTP stream."""
    iterator = events.__aiter__()
    pending: asyncio.Task[dict[str, Any]] | None = None
    buffered: list[dict[str, Any]] = []
    last_flush = time.monotonic()
    outcome = "failed"
    message: str | None = "The assistant stopped before finishing."
    try:
        while True:
            record = store.get(actor, job_id)
            if record["state"] in TERMINAL:
                return
            if store.cancelled(actor, job_id):
                outcome, message = "cancelled", "Stopped at your request."
                break
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=1.0)
            if done:
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                if (
                    event.get("type") == "text"
                    and buffered
                    and (buffered[-1].get("type") == "text")
                ):
                    buffered[-1]["delta"] += event["delta"]
                else:
                    buffered.append(event)
                if event.get("type") == "complete":
                    outcome, message = "completed", None
                elif event.get("type") == "error":
                    outcome, message = "failed", str(event.get("message"))
            if (
                buffered and (len(buffered) >= 20 or time.monotonic() - last_flush >= 1)
            ) or time.monotonic() - last_flush >= HEARTBEAT_SECONDS:
                store.append(actor, job_id, buffered)
                buffered = []
                last_flush = time.monotonic()
        store.append(actor, job_id, buffered)
        store.finish(actor, job_id, outcome, message)
    except Exception:
        log.exception("background chat failed job=%s actor=%s", job_id, actor[:8])
        store.finish(
            actor,
            job_id,
            "failed",
            "The background analysis failed. Saved progress is available; try again.",
        )
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


def start_worker(
    store: ChatJobStore,
    actor: str,
    job_id: str,
    source: Callable[[], AsyncIterator[dict[str, Any]]],
    *,
    register: Callable[[], int] | None = None,
    complete: Callable[[int], object] | None = None,
) -> None:
    if not store.claim(actor, job_id):
        return
    try:
        task_id = register() if register is not None else None
    except Exception:
        store.finish(
            actor, job_id, "failed", "Could not register the background worker."
        )
        raise

    def run() -> None:
        try:
            asyncio.run(execute_job(store, actor, job_id, source()))
        finally:
            if task_id is not None and complete is not None:
                complete(task_id)

    try:
        threading.Thread(
            target=run,
            name=f"chat-{hashlib.sha256(job_id.encode()).hexdigest()[:8]}",
            daemon=True,
        ).start()
    except Exception:
        if task_id is not None and complete is not None:
            complete(task_id)
        store.finish(actor, job_id, "failed", "Could not start the background worker.")
        raise
