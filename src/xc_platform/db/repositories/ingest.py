"""Ingest run lifecycle state (Requirement 7.7-7.8, Task 4.4)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.base import BaseRepository

# Allowed state transitions (Requirement 7.7: an ingest run's status is
# retained and meaningful, not an arbitrary free-text field).
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"discovering", "failed"},
    "discovering": {"staging", "failed"},
    "staging": {"awaiting_review", "failed"},
    "awaiting_review": {"committing", "failed"},
    "committing": {"committed", "failed"},
    "committed": {"rolled_back"},
    "failed": set(),
    "rolled_back": set(),
}


@dataclass(frozen=True, slots=True)
class IngestRunRecord:
    ingest_run_id: str
    source_id: str
    submitted_url: str
    adapter_type: str
    state: str
    inserted_count: int
    updated_count: int
    unchanged_count: int
    quarantined_count: int
    requests_used: int
    tavily_credits_used: int
    started_at: str
    finished_at: str | None
    result_publication_id: str | None
    error_summary: str | None
    correlation_id: str


def _row_to_record(row: sqlite3.Row) -> IngestRunRecord:
    return IngestRunRecord(
        ingest_run_id=row["ingest_run_id"],
        source_id=row["source_id"],
        submitted_url=row["submitted_url"],
        adapter_type=row["adapter_type"],
        state=row["state"],
        inserted_count=row["inserted_count"],
        updated_count=row["updated_count"],
        unchanged_count=row["unchanged_count"],
        quarantined_count=row["quarantined_count"],
        requests_used=row["requests_used"],
        tavily_credits_used=row["tavily_credits_used"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        result_publication_id=row["result_publication_id"],
        error_summary=row["error_summary"],
        correlation_id=row["correlation_id"],
    )


class IngestRunRepository(BaseRepository):
    def create_run(
        self,
        *,
        source_id: str,
        submitted_url: str,
        adapter_type: str,
        correlation_id: str,
        requested_scope_json: str | None = None,
    ) -> str:
        ingest_run_id = new_id()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO ingest_runs (ingest_run_id, source_id, "
                "submitted_url, requested_scope_json, adapter_type, state, "
                "started_at, correlation_id) "
                "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    ingest_run_id,
                    source_id,
                    submitted_url,
                    requested_scope_json,
                    adapter_type,
                    utc_now_iso(),
                    correlation_id,
                ),
            )
        return ingest_run_id

    def get(self, ingest_run_id: str) -> IngestRunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM ingest_runs WHERE ingest_run_id = ?", (ingest_run_id,)
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_recent(self, *, limit: int = 25) -> list[IngestRunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def transition(
        self, ingest_run_id: str, new_state: str, *, error_summary: str | None = None
    ) -> None:
        """Move an ingest run to ``new_state``, enforcing the state machine.

        Terminal states (``failed``, ``committed`` other than its one
        allowed rollback, ``rolled_back``) record ``finished_at``.
        """
        current = self.get(ingest_run_id)
        if current is None:
            raise RepositoryError(f"No ingest_run with id {ingest_run_id!r}.")

        allowed = _ALLOWED_TRANSITIONS.get(current.state, set())
        if new_state not in allowed:
            raise RepositoryError(
                f"Cannot transition ingest_run {ingest_run_id!r} from "
                f"{current.state!r} to {new_state!r}; allowed: {sorted(allowed)}."
            )

        finished_at = (
            utc_now_iso()
            if new_state in {"committed", "failed", "rolled_back"}
            else None
        )
        with self.transaction() as conn:
            conn.execute(
                "UPDATE ingest_runs SET state = ?, finished_at = "
                "COALESCE(?, finished_at), error_summary = COALESCE(?, error_summary) "
                "WHERE ingest_run_id = ?",
                (new_state, finished_at, error_summary, ingest_run_id),
            )

    def record_counts(
        self,
        ingest_run_id: str,
        *,
        inserted: int = 0,
        updated: int = 0,
        unchanged: int = 0,
        quarantined: int = 0,
    ) -> None:
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE ingest_runs SET inserted_count = inserted_count + ?, "
                "updated_count = updated_count + ?, "
                "unchanged_count = unchanged_count + ?, "
                "quarantined_count = quarantined_count + ? "
                "WHERE ingest_run_id = ?",
                (inserted, updated, unchanged, quarantined, ingest_run_id),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(f"No ingest_run with id {ingest_run_id!r}.")

    def record_usage(
        self,
        ingest_run_id: str,
        *,
        requests_used: int = 0,
        tavily_credits_used: int = 0,
    ) -> None:
        """Increment operational-usage counters (Requirement 6.7, 15).

        These are runaway-protection telemetry, not a dollar cost -- see the
        2026-09-20 owner decision removing cost gating from the spec.
        """
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE ingest_runs SET requests_used = requests_used + ?, "
                "tavily_credits_used = tavily_credits_used + ? "
                "WHERE ingest_run_id = ?",
                (requests_used, tavily_credits_used, ingest_run_id),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(f"No ingest_run with id {ingest_run_id!r}.")

    def attach_publication(self, ingest_run_id: str, publication_id: str) -> None:
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE ingest_runs SET result_publication_id = ? "
                "WHERE ingest_run_id = ?",
                (publication_id, ingest_run_id),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(f"No ingest_run with id {ingest_run_id!r}.")
