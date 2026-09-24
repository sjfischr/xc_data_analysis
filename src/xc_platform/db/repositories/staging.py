"""Source registration and staging writes (Requirement 7, Task 4.4).

This is the write path an ingestion adapter uses before anything touches
canonical tables: register (or reuse) a source, record the immutable raw
object an ingest run retrieved, and stage the rows extracted from it. Staged
rows are never promoted to ``results`` by this repository -- that requires a
resolution decision and is out of scope here (Requirement 7.6, Task 10).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class StagedResultRecord:
    staged_result_id: str
    ingest_run_id: str
    source_object_id: str
    source_id: str
    source_result_id: str | None
    idempotency_key: str
    raw_fields_json: str
    candidate_fields_json: str
    validation_state: str
    validation_warnings_json: str | None
    conflicts_with_result_id: str | None
    created_at: str


def _row_to_staged_result(row: sqlite3.Row) -> StagedResultRecord:
    return StagedResultRecord(
        staged_result_id=row["staged_result_id"],
        ingest_run_id=row["ingest_run_id"],
        source_object_id=row["source_object_id"],
        source_id=row["source_id"],
        source_result_id=row["source_result_id"],
        idempotency_key=row["idempotency_key"],
        raw_fields_json=row["raw_fields_json"],
        candidate_fields_json=row["candidate_fields_json"],
        validation_state=row["validation_state"],
        validation_warnings_json=row["validation_warnings_json"],
        conflicts_with_result_id=row["conflicts_with_result_id"],
        created_at=row["created_at"],
    )


class StagingRepository(BaseRepository):
    def get_or_create_source(
        self, *, source_namespace: str, adapter_type: str, base_domain: str
    ) -> str:
        """Return the ``source_id`` for ``source_namespace``, creating it if new."""
        existing = self._conn.execute(
            "SELECT source_id FROM data_sources WHERE source_namespace = ?",
            (source_namespace,),
        ).fetchone()
        if existing is not None:
            return str(existing["source_id"])

        source_id = new_id()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO data_sources "
                "(source_id, source_namespace, adapter_type, base_domain, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, source_namespace, adapter_type, base_domain, utc_now_iso()),
            )
        return source_id

    def record_source_object(
        self,
        *,
        source_id: str,
        ingest_run_id: str,
        source_url: str,
        raw_s3_key: str,
        content_sha256: str,
        byte_size: int,
        media_type: str,
        extraction_strategy: str,
        request_id: str | None = None,
    ) -> str:
        """Record an immutable raw payload reference and return its ID.

        If this exact payload (by content hash, within this source) was
        already recorded, returns the existing ID instead of inserting a
        duplicate (Requirement 4.6: unchanged re-imports produce no
        duplicate entities).
        """
        existing = self._conn.execute(
            "SELECT source_object_id FROM source_objects "
            "WHERE source_id = ? AND content_sha256 = ?",
            (source_id, content_sha256),
        ).fetchone()
        if existing is not None:
            return str(existing["source_object_id"])

        source_object_id = new_id()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO source_objects (source_object_id, source_id, "
                "ingest_run_id, source_url, raw_s3_key, content_sha256, "
                "byte_size, media_type, extraction_strategy, retrieved_at, "
                "request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_object_id,
                    source_id,
                    ingest_run_id,
                    source_url,
                    raw_s3_key,
                    content_sha256,
                    byte_size,
                    media_type,
                    extraction_strategy,
                    utc_now_iso(),
                    request_id,
                ),
            )
        return source_object_id

    def stage_result(
        self,
        *,
        ingest_run_id: str,
        source_object_id: str,
        source_id: str,
        idempotency_key: str,
        raw_fields_json: str,
        candidate_fields_json: str,
        source_result_id: str | None = None,
        validation_state: str = "pending",
        validation_warnings_json: str | None = None,
    ) -> str:
        staged_result_id = new_id()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO staged_results (staged_result_id, ingest_run_id, "
                "source_object_id, source_id, source_result_id, idempotency_key, "
                "raw_fields_json, candidate_fields_json, validation_state, "
                "validation_warnings_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    staged_result_id,
                    ingest_run_id,
                    source_object_id,
                    source_id,
                    source_result_id,
                    idempotency_key,
                    raw_fields_json,
                    candidate_fields_json,
                    validation_state,
                    validation_warnings_json,
                    utc_now_iso(),
                ),
            )
        return staged_result_id

    def mark_valid(self, staged_result_id: str) -> None:
        """Mark a staged row as passing validation (Task 12.2) -- ready for
        entity resolution, distinct from the initial ``'pending'`` default
        and from ``'quarantined'``."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE staged_results SET validation_state = 'valid' "
                "WHERE staged_result_id = ?",
                (staged_result_id,),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(
                    f"No staged_result with id {staged_result_id!r} to validate."
                )

    def find_by_idempotency_key(
        self, *, source_id: str, idempotency_key: str
    ) -> StagedResultRecord | None:
        """The most recent staged row for this exact source identity, if
        any -- Task 12.2's "unchanged re-import" and same-identity-changed-
        content conflict detection both start here."""
        row = self._conn.execute(
            "SELECT * FROM staged_results WHERE source_id = ? AND idempotency_key = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (source_id, idempotency_key),
        ).fetchone()
        return None if row is None else _row_to_staged_result(row)

    def quarantine(self, staged_result_id: str, *, reason: str) -> None:
        """Mark a staged row quarantined with a warning explaining why.

        Missing or invalid required fields are never guessed at (Requirement
        7.2) -- they are staged with the gap explicit and quarantined here
        for administrator review.
        """
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE staged_results SET validation_state = 'quarantined', "
                "validation_warnings_json = ? WHERE staged_result_id = ?",
                (reason, staged_result_id),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(
                    f"No staged_result with id {staged_result_id!r} to quarantine."
                )

    def mark_committed(
        self, staged_result_id: str, *, conflicts_with_result_id: str | None = None
    ) -> None:
        """Mark a staged row as promoted to a canonical result."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE staged_results SET validation_state = 'committed', "
                "conflicts_with_result_id = COALESCE(?, conflicts_with_result_id) "
                "WHERE staged_result_id = ?",
                (conflicts_with_result_id, staged_result_id),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(
                    f"No staged_result with id {staged_result_id!r} to commit."
                )

    def list_by_ingest_run(
        self, ingest_run_id: str, *, validation_state: str | None = None
    ) -> list[StagedResultRecord]:
        if validation_state is None:
            rows = self._conn.execute(
                "SELECT * FROM staged_results WHERE ingest_run_id = ? "
                "ORDER BY created_at",
                (ingest_run_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM staged_results WHERE ingest_run_id = ? "
                "AND validation_state = ? ORDER BY created_at",
                (ingest_run_id, validation_state),
            ).fetchall()
        return [_row_to_staged_result(row) for row in rows]
