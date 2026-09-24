"""Repository tests: lifecycle flows, rollback, and constraint enforcement.

Task 4.4 explicitly requires transaction-rollback and constraint tests, on
top of exercising each repository's typed interface.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from xc_platform.db.errors import RepositoryError
from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.publications import PublicationRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository

# --- staging + ingest + publication end-to-end lifecycle -------------------


def test_full_ingest_to_publication_lifecycle(conn: sqlite3.Connection) -> None:
    staging = StagingRepository(conn)
    ingest = IngestRunRepository(conn)
    publications = PublicationRepository(conn)

    source_id = staging.get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    # Reusing the same namespace returns the same source, not a duplicate.
    assert (
        staging.get_or_create_source(
            source_namespace="runsignup",
            adapter_type="runsignup",
            base_domain="runsignup.com",
        )
        == source_id
    )

    run_id = ingest.create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/154050",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    assert ingest.get(run_id).state == "pending"  # type: ignore[union-attr]

    source_object_id = staging.record_source_object(
        source_id=source_id,
        ingest_run_id=run_id,
        source_url="https://runsignup.com/Race/Results/154050",
        raw_s3_key="raw/runsignup/2026/abc123.json.gz",
        content_sha256="abc123",
        byte_size=1024,
        media_type="application/json",
        extraction_strategy="runsignup_rest",
    )

    staged_id = staging.stage_result(
        ingest_run_id=run_id,
        source_object_id=source_object_id,
        source_id=source_id,
        idempotency_key="runsignup:691534:1001",
        raw_fields_json=json.dumps({"place": "1"}),
        candidate_fields_json=json.dumps({"place_overall": 1}),
        source_result_id="1001",
    )
    staged = staging.list_by_ingest_run(run_id)
    assert [s.staged_result_id for s in staged] == [staged_id]

    ingest.transition(run_id, "discovering")
    ingest.transition(run_id, "staging")
    ingest.record_counts(run_id, inserted=1)
    ingest.record_usage(run_id, requests_used=3)
    ingest.transition(run_id, "awaiting_review")
    ingest.transition(run_id, "committing")

    publication_id = new_id()
    publications.record_publication(
        publication_id=publication_id,
        snapshot_key=f"database/snapshots/{publication_id}/xc.db",
        snapshot_version_id="v1",
        content_sha256="deadbeef",
        byte_size=2048,
        schema_version=2,
        summary_json=json.dumps({"results": 1}),
        created_by_ingest_run_id=run_id,
    )
    ingest.attach_publication(run_id, publication_id)
    ingest.transition(run_id, "committed")

    record = ingest.get(run_id)
    assert record is not None
    assert record.state == "committed"
    assert record.inserted_count == 1
    assert record.requests_used == 3
    assert record.result_publication_id == publication_id
    assert record.finished_at is not None

    active = publications.get_active()
    assert active is not None
    assert active.publication_id == publication_id
    assert active.created_by_ingest_run_id == run_id


def test_publishing_a_new_generation_supersedes_the_parent(
    conn: sqlite3.Connection,
) -> None:
    publications = PublicationRepository(conn)
    first_id = new_id()
    publications.record_publication(
        publication_id=first_id,
        snapshot_key="database/snapshots/1/xc.db",
        snapshot_version_id="v1",
        content_sha256="hash1",
        byte_size=100,
        schema_version=2,
        summary_json="{}",
    )
    second_id = new_id()
    publications.record_publication(
        publication_id=second_id,
        parent_publication_id=first_id,
        snapshot_key="database/snapshots/2/xc.db",
        snapshot_version_id="v2",
        content_sha256="hash2",
        byte_size=200,
        schema_version=2,
        summary_json="{}",
    )

    assert publications.get(first_id).status == "superseded"  # type: ignore[union-attr]
    active = publications.get_active()
    assert active is not None
    assert active.publication_id == second_id


def test_ingest_run_rejects_invalid_state_transition(conn: sqlite3.Connection) -> None:
    staging = StagingRepository(conn)
    ingest = IngestRunRepository(conn)
    source_id = staging.get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    run_id = ingest.create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/x",
        adapter_type="runsignup",
        correlation_id="corr-2",
    )
    with pytest.raises(RepositoryError, match="Cannot transition"):
        ingest.transition(run_id, "committed")


def test_quarantine_requires_an_existing_staged_result(
    conn: sqlite3.Connection,
) -> None:
    staging = StagingRepository(conn)
    with pytest.raises(RepositoryError):
        staging.quarantine("does-not-exist", reason="missing required field")


# --- resolution --------------------------------------------------------


def test_resolution_case_approve_then_reverse(conn: sqlite3.Connection) -> None:
    resolution = ResolutionRepository(conn)
    case_id = resolution.open_case(
        entity_type="athlete",
        evidence_json=json.dumps({"candidates": ["a1", "a2"]}),
        confidence=0.62,
    )
    assert resolution.get_case(case_id).status == "pending"  # type: ignore[union-attr]
    assert [c.resolution_case_id for c in resolution.list_pending_cases()] == [case_id]

    decision_id = resolution.record_decision(
        resolution_case_id=case_id,
        decision_type="approve",
        actor="admin@example.com",
        evidence_summary="matched on bib + school + grade progression",
        affected_records_json=json.dumps(["athlete:a1"]),
    )
    assert resolution.get_case(case_id).status == "approved"  # type: ignore[union-attr]
    assert resolution.list_pending_cases() == []

    # A mistaken merge can be corrected without losing the original decision
    # (Requirement 8.10).
    reversal_id = resolution.record_decision(
        resolution_case_id=case_id,
        decision_type="reverse",
        actor="admin@example.com",
        evidence_summary="coach flagged this as two distinct athletes",
        affected_records_json=json.dumps(["athlete:a1"]),
        reversal_of_decision_id=decision_id,
    )
    assert reversal_id != decision_id
    assert resolution.get_case(case_id).status == "reversed"  # type: ignore[union-attr]


def test_resolution_decision_rejected_on_non_pending_case(
    conn: sqlite3.Connection,
) -> None:
    resolution = ResolutionRepository(conn)
    case_id = resolution.open_case(entity_type="school", evidence_json="{}")
    resolution.record_decision(
        resolution_case_id=case_id,
        decision_type="reject",
        actor="admin@example.com",
        evidence_summary="not a match",
        affected_records_json="[]",
    )
    with pytest.raises(RepositoryError, match="not 'pending'"):
        resolution.record_decision(
            resolution_case_id=case_id,
            decision_type="approve",
            actor="admin@example.com",
            evidence_summary="changed my mind",
            affected_records_json="[]",
        )


# --- transaction rollback and constraint enforcement ------------------


def test_staging_a_result_for_a_missing_ingest_run_rolls_back(
    conn: sqlite3.Connection,
) -> None:
    staging = StagingRepository(conn)
    with pytest.raises(sqlite3.IntegrityError):
        staging.stage_result(
            ingest_run_id="does-not-exist",
            source_object_id="also-missing",
            source_id="also-missing",
            idempotency_key="k",
            raw_fields_json="{}",
            candidate_fields_json="{}",
        )
    count = conn.execute("SELECT COUNT(*) AS n FROM staged_results").fetchone()["n"]
    assert count == 0


def test_unique_source_result_id_is_enforced(conn: sqlite3.Connection) -> None:
    now = utc_now_iso()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO data_sources (source_id, source_namespace, adapter_type, "
        "base_domain, created_at) VALUES ('s1', 'runsignup', 'runsignup', "
        "'runsignup.com', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO meets (meet_id, season_year, name, created_at, updated_at) "
        "VALUES ('m1', 2026, 'Test Meet', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO races (race_id, meet_id, division_code, gender_code, "
        "created_at, updated_at) VALUES ('r1', 'm1', 'varsity', 'M', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO schools (school_id, canonical_name, display_name, "
        "created_at, updated_at) VALUES ('sc1', 'Test School', 'Test School', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO athletes (athlete_id, canonical_first_name, "
        "canonical_last_name, display_name, created_at, updated_at) "
        "VALUES ('a1', 'Jane', 'Doe', 'Jane Doe', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO athletes (athlete_id, canonical_first_name, "
        "canonical_last_name, display_name, created_at, updated_at) "
        "VALUES ('a2', 'Jo', 'Roe', 'Jo Roe', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO ingest_runs (ingest_run_id, source_id, submitted_url, "
        "adapter_type, state, started_at, correlation_id) "
        "VALUES ('run1', 's1', 'https://runsignup.com/x', 'runsignup', "
        "'pending', ?, 'corr')",
        (now,),
    )
    conn.execute("COMMIT")

    def insert_result(result_id: str, source_result_id: str, athlete_id: str) -> None:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO results (result_id, source_id, source_result_id, "
            "race_id, athlete_id, school_id, ingest_run_id, created_at, "
            "updated_at) VALUES (?, 's1', ?, 'r1', ?, 'sc1', 'run1', ?, ?)",
            (result_id, source_result_id, athlete_id, now, now),
        )
        conn.execute("COMMIT")

    insert_result("res1", "1001", "a1")

    # Same source + source_result_id, different athlete: must be refused --
    # this is the upstream-identifier uniqueness constraint (Requirement 2.3).
    with pytest.raises(sqlite3.IntegrityError):
        insert_result("res2", "1001", "a2")
    conn.execute("ROLLBACK")

    # One result per (race, athlete) unless a documented race format sets
    # multi_result_reason (design.md section 8.2 / Requirement 8).
    with pytest.raises(sqlite3.IntegrityError):
        insert_result("res3", "1002", "a1")
    conn.execute("ROLLBACK")

    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO results (result_id, source_id, source_result_id, "
        "race_id, athlete_id, school_id, ingest_run_id, multi_result_reason, "
        "created_at, updated_at) VALUES ('res4', 's1', '1003', 'r1', 'a1', "
        "'sc1', 'run1', 'multi-wave format', ?, ?)",
        (now, now),
    )
    conn.execute("COMMIT")

    count = conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()["n"]
    assert count == 2


# --- canonical reads -----------------------------------------------------


def test_canonical_reads_and_unambiguous_alias_resolution(
    conn: sqlite3.Connection,
) -> None:
    now = utc_now_iso()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO data_sources (source_id, source_namespace, adapter_type, "
        "base_domain, created_at) VALUES ('s1', 'runsignup', 'runsignup', "
        "'runsignup.com', ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO schools (school_id, canonical_name, display_name, "
        "created_at, updated_at) VALUES ('sc1', 'Saint Sebastian', "
        "'Saint Sebastian', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO school_aliases (school_alias_id, school_id, source_id, "
        "raw_value, normalized_value, created_at) VALUES ('al1', 'sc1', "
        "'s1', 'St. Sebastian', 'st sebastian', ?)",
        (now,),
    )
    # An ambiguous alias with the same normalized value must not resolve.
    conn.execute(
        "INSERT INTO school_aliases (school_alias_id, school_id, source_id, "
        "raw_value, normalized_value, is_ambiguous, created_at) VALUES "
        "('al2', 'sc1', 's1', 'Saint S.', 'saint s', 1, ?)",
        (now,),
    )
    conn.execute("COMMIT")

    canonical = CanonicalReadRepository(conn)
    school = canonical.get_school_by_canonical_name("Saint Sebastian")
    assert school is not None
    assert school.school_id == "sc1"

    resolved = canonical.resolve_school_alias(
        source_id="s1", normalized_value="st sebastian"
    )
    assert resolved == "sc1"

    ambiguous = canonical.resolve_school_alias(
        source_id="s1", normalized_value="saint s"
    )
    assert ambiguous is None
