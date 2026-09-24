"""Publication lineage metadata (Requirement 3, Task 4.4).

This mirrors, inside the SQLite file itself, the lineage that S3's
``active.json`` manifest tracks authoritatively (design.md section 7.1-7.3).
S3's manifest is the source of truth for "what to open"; this table lets the
database answer "how did I get here" without a round trip to S3.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import utc_now_iso
from xc_platform.db.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    publication_id: str
    parent_publication_id: str | None
    snapshot_key: str
    snapshot_version_id: str
    content_sha256: str
    byte_size: int
    schema_version: int
    status: str
    created_at: str
    created_by_ingest_run_id: str | None
    summary_json: str


def _row_to_record(row: sqlite3.Row) -> PublicationRecord:
    return PublicationRecord(
        publication_id=row["publication_id"],
        parent_publication_id=row["parent_publication_id"],
        snapshot_key=row["snapshot_key"],
        snapshot_version_id=row["snapshot_version_id"],
        content_sha256=row["content_sha256"],
        byte_size=row["byte_size"],
        schema_version=row["schema_version"],
        status=row["status"],
        created_at=row["created_at"],
        created_by_ingest_run_id=row["created_by_ingest_run_id"],
        summary_json=row["summary_json"],
    )


class PublicationRepository(BaseRepository):
    def record_publication(
        self,
        *,
        publication_id: str,
        snapshot_key: str,
        snapshot_version_id: str,
        content_sha256: str,
        byte_size: int,
        schema_version: int,
        summary_json: str,
        parent_publication_id: str | None = None,
        created_by_ingest_run_id: str | None = None,
    ) -> None:
        """Record a new publication and mark its parent superseded.

        Called by the writer *after* a snapshot has been uploaded and
        ``active.json`` compare-and-swapped (design.md section 7.3) -- this
        is bookkeeping about a publication that already happened, not the
        publish operation itself.
        """
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO db_publications (publication_id, "
                "parent_publication_id, snapshot_key, snapshot_version_id, "
                "content_sha256, byte_size, schema_version, status, "
                "created_at, created_by_ingest_run_id, summary_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (
                    publication_id,
                    parent_publication_id,
                    snapshot_key,
                    snapshot_version_id,
                    content_sha256,
                    byte_size,
                    schema_version,
                    utc_now_iso(),
                    created_by_ingest_run_id,
                    summary_json,
                ),
            )
            if parent_publication_id is not None:
                conn.execute(
                    "UPDATE db_publications SET status = 'superseded' "
                    "WHERE publication_id = ? AND status = 'active'",
                    (parent_publication_id,),
                )

    def get(self, publication_id: str) -> PublicationRecord | None:
        row = self._conn.execute(
            "SELECT * FROM db_publications WHERE publication_id = ?",
            (publication_id,),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def get_active(self) -> PublicationRecord | None:
        row = self._conn.execute(
            "SELECT * FROM db_publications WHERE status = 'active' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def mark_orphaned(self, publication_id: str) -> None:
        """Mark a publication candidate that lost a compare-and-swap race.

        Design.md section 7.3: when the manifest compare-and-swap fails, the
        uploaded snapshot is left in place but marked for lifecycle cleanup
        rather than becoming active.
        """
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE db_publications SET status = 'orphaned' "
                "WHERE publication_id = ?",
                (publication_id,),
            )
            if cursor.rowcount == 0:
                raise RepositoryError(f"No publication with id {publication_id!r}.")

    def list_lineage(self, *, limit: int = 50) -> list[PublicationRecord]:
        rows = self._conn.execute(
            "SELECT * FROM db_publications ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_record(row) for row in rows]
