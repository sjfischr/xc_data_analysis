"""Resolution cases and decisions (Requirement 8, Task 4.4).

SQLite alias and decision records are the authoritative resolution history
(Requirement 8.8) -- not conversational agent memory. This repository is the
only way a case is opened or a decision recorded; deterministic matching and
agent candidate generation happen elsewhere and call in here once a
disposition is reached.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.base import BaseRepository

_CASE_STATUS_BY_DECISION = {
    "approve": "approved",
    "reject": "rejected",
    "split": "split",
    "reverse": "reversed",
}


@dataclass(frozen=True, slots=True)
class ResolutionCaseRecord:
    resolution_case_id: str
    entity_type: str
    ingest_run_id: str | None
    candidate_entity_id: str | None
    evidence_json: str
    confidence: float | None
    status: str
    created_at: str
    updated_at: str


def _row_to_case(row: sqlite3.Row) -> ResolutionCaseRecord:
    return ResolutionCaseRecord(
        resolution_case_id=row["resolution_case_id"],
        entity_type=row["entity_type"],
        ingest_run_id=row["ingest_run_id"],
        candidate_entity_id=row["candidate_entity_id"],
        evidence_json=row["evidence_json"],
        confidence=row["confidence"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ResolutionRepository(BaseRepository):
    def open_case(
        self,
        *,
        entity_type: str,
        evidence_json: str,
        ingest_run_id: str | None = None,
        candidate_entity_id: str | None = None,
        confidence: float | None = None,
    ) -> str:
        """Open a review-queue item. Never merges entities by itself.

        Requirement 8.5: conflicting or low-confidence candidate evidence is
        routed here for human disposition, not automatically applied.
        """
        resolution_case_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO resolution_cases (resolution_case_id, entity_type, "
                "ingest_run_id, candidate_entity_id, evidence_json, confidence, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    resolution_case_id,
                    entity_type,
                    ingest_run_id,
                    candidate_entity_id,
                    evidence_json,
                    confidence,
                    now,
                    now,
                ),
            )
        return resolution_case_id

    def find_pending_case(
        self,
        *,
        ingest_run_id: str | None,
        entity_type: str,
        evidence_match: dict[str, str],
    ) -> ResolutionCaseRecord | None:
        """An already-open case for the same raw identity in the same run
        (Task 19.3): resolution re-runs on every commit attempt, and must
        not open a duplicate case each time."""
        conditions = ["status = 'pending'", "entity_type = ?", "ingest_run_id IS ?"]
        params: list[object] = [entity_type, ingest_run_id]
        for key in sorted(evidence_match):
            if not key.isidentifier():
                raise RepositoryError(f"invalid evidence key {key!r}")
            conditions.append(f"json_extract(evidence_json, '$.{key}') = ?")
            params.append(evidence_match[key])
        # Keys are validated identifiers; values go through `params`.
        row = self._conn.execute(
            "SELECT * FROM resolution_cases WHERE "  # noqa: S608
            + " AND ".join(conditions)
            + " ORDER BY created_at LIMIT 1",
            params,
        ).fetchone()
        return None if row is None else _row_to_case(row)

    def get_case(self, resolution_case_id: str) -> ResolutionCaseRecord | None:
        row = self._conn.execute(
            "SELECT * FROM resolution_cases WHERE resolution_case_id = ?",
            (resolution_case_id,),
        ).fetchone()
        return None if row is None else _row_to_case(row)

    def list_pending_cases(
        self, *, entity_type: str | None = None
    ) -> list[ResolutionCaseRecord]:
        if entity_type is None:
            rows = self._conn.execute(
                "SELECT * FROM resolution_cases WHERE status = 'pending' "
                "ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM resolution_cases WHERE status = 'pending' "
                "AND entity_type = ? ORDER BY created_at",
                (entity_type,),
            ).fetchall()
        return [_row_to_case(row) for row in rows]

    def list_pending_cases_for_ingest_run(
        self, ingest_run_id: str
    ) -> list[ResolutionCaseRecord]:
        """Every still-open case a specific ingest run opened -- the commit
        gate (Task 12.4) checks this is empty before applying a run."""
        rows = self._conn.execute(
            "SELECT * FROM resolution_cases WHERE status = 'pending' "
            "AND ingest_run_id = ? ORDER BY created_at",
            (ingest_run_id,),
        ).fetchall()
        return [_row_to_case(row) for row in rows]

    def record_decision(
        self,
        *,
        resolution_case_id: str,
        decision_type: str,
        actor: str,
        evidence_summary: str,
        affected_records_json: str,
        policy_version: str | None = None,
        reversal_of_decision_id: str | None = None,
    ) -> str:
        """Record an approve/reject/split/reverse decision on a case.

        Preserves the decision, actor, timestamp, evidence summary, and
        affected records (Requirement 8.6) and advances the case's status
        accordingly. Raises if the case does not exist or is not pending
        (except for ``reverse``, which is the documented path to correct a
        mistaken merge -- Requirement 8.10 -- and so may act on an already
        ``approved`` case).
        """
        if decision_type not in _CASE_STATUS_BY_DECISION:
            raise RepositoryError(f"Unknown decision_type {decision_type!r}.")

        case = self.get_case(resolution_case_id)
        if case is None:
            raise RepositoryError(f"No resolution_case with id {resolution_case_id!r}.")
        if decision_type != "reverse" and case.status != "pending":
            raise RepositoryError(
                f"Cannot apply decision {decision_type!r} to case "
                f"{resolution_case_id!r}: status is {case.status!r}, not 'pending'."
            )
        if decision_type == "reverse" and case.status != "approved":
            raise RepositoryError(
                f"Cannot reverse case {resolution_case_id!r}: status is "
                f"{case.status!r}, not 'approved'."
            )

        resolution_decision_id = new_id()
        now = utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO resolution_decisions (resolution_decision_id, "
                "resolution_case_id, decision_type, actor, evidence_summary, "
                "policy_version, affected_records_json, reversal_of_decision_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resolution_decision_id,
                    resolution_case_id,
                    decision_type,
                    actor,
                    evidence_summary,
                    policy_version,
                    affected_records_json,
                    reversal_of_decision_id,
                    now,
                ),
            )
            conn.execute(
                "UPDATE resolution_cases SET status = ?, updated_at = ? "
                "WHERE resolution_case_id = ?",
                (_CASE_STATUS_BY_DECISION[decision_type], now, resolution_case_id),
            )
        return resolution_decision_id
