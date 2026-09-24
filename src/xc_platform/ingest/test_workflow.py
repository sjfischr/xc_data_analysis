"""End-to-end intake workflow tests (Task 12.5): a full supplied-URL import
from discovery through commit and publication, frozen-season refusal,
unchanged re-import producing zero duplicates, and injected extraction/
transaction/publication failures leaving no partial state.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.publication.s3_client import FakeS3Client
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.ingest.adapters.runsignup import RunSignupAdapter
from xc_platform.ingest.adapters.runsignup_client import RequestBudget, RunSignupClient
from xc_platform.ingest.raw_storage import RawObjectStore
from xc_platform.ingest.source_router import (
    UnsupportedSourceError,
    normalize_intake_url,
    select_adapter,
)
from xc_platform.ingest.workflow import (
    IngestRunNotReadyError,
    commit_scope,
    discover_scope,
    resolve_scope,
    stage_scope,
)

RACE_ID = "154050"  # MEET_RACE_IDS["154050"] == 1, matching existing adapter tests
URL = f"https://runsignup.com/Race/Results/{RACE_ID}"


@dataclass(frozen=True, slots=True)
class _Resp:
    status: int
    body: bytes


def _scripted_client(responses: dict[str, dict[str, Any]]) -> RunSignupClient:
    def fetcher(url: str) -> Any:
        for prefix, payload in responses.items():
            if prefix in url:
                return _Resp(status=200, body=json.dumps(payload).encode("utf-8"))
        raise AssertionError(f"unscripted URL: {url}")

    return RunSignupClient(
        budget=RequestBudget(max_requests=1000, max_wall_time_s=60.0), fetcher=fetcher
    )


def _race_meta(*, event_year: int) -> dict[str, Any]:
    return {
        "race": {
            "name": "NVJCYO Meet 1",
            "events": [
                {
                    "event_id": 1,
                    "name": "Varsity Girls",
                    "start_time": f"9/1/{event_year}",
                }
            ],
        }
    }


_RESULT_SETS = {
    "individual_results_sets": [
        {
            "individual_result_set_id": 10,
            "individual_result_set_name": "Varsity Girls",
            "public_results": "T",
            "preliminary_results": "F",
        }
    ]
}


def _results_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "individual_results_sets": [
            {
                "results_headers": {
                    "custom-field-100001": "Team Name",
                    "custom-field-100002": "Year (Grade)",
                },
                "results": rows,
            }
        ]
    }


def _row(
    result_id: int, *, first: str, last: str, team: str, place: int, time_text: str
) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "first_name": first,
        "last_name": last,
        "gender": "F",
        "place": str(place),
        "chip_time": time_text,
        "bib": str(100 + result_id),
        "custom-field-100001": team,
        "custom-field-100002": "6",
    }


_ROWS = [
    _row(5001, first="Jane", last="Doe", team="St Agnes", place=1, time_text="21:30"),
    _row(
        5002, first="Amy", last="Smith", team="Holy Family", place=2, time_text="22:10"
    ),
]


def _client_for(*, event_year: int, rows: list[dict[str, Any]]) -> RunSignupClient:
    return _scripted_client(
        {
            f"race/{RACE_ID}?format": _race_meta(event_year=event_year),
            "get-result-sets?format=json&event_id=1": _RESULT_SETS,
            "get-results?format=json": _results_page(rows),
        }
    )


@dataclass
class Harness:
    conn: sqlite3.Connection
    db_path: Path
    staging: StagingRepository
    ingest: IngestRunRepository
    resolver: ResolutionRepository
    canonical: CanonicalReadRepository
    canonical_write: CanonicalWriteRepository
    s3: FakeS3Client
    raw_store: RawObjectStore
    publisher: SnapshotPublisher


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    db_path = tmp_path / "xc.db"
    conn = open_writer_connection(db_path)
    migrate(conn)
    s3 = FakeS3Client()
    return Harness(
        conn=conn,
        db_path=db_path,
        staging=StagingRepository(conn),
        ingest=IngestRunRepository(conn),
        resolver=ResolutionRepository(conn),
        canonical=CanonicalReadRepository(conn),
        canonical_write=CanonicalWriteRepository(conn),
        s3=s3,
        raw_store=RawObjectStore(s3, work_dir=tmp_path / "raw-work"),
        publisher=SnapshotPublisher(
            s3, work_dir=tmp_path / "pub-work", owner_id="test"
        ),
    )


def _run_full_import(
    h: Harness, *, event_year: int = 2026, rows: list[dict[str, Any]] | None = None
) -> tuple[str, str]:
    """Drives discover -> stage -> resolve -> commit for the golden path.
    Returns (ingest_run_id, publication_id).

    The league's schools already exist, as in production: since Task 19.3
    an unmatched school name opens a review case (unless the database has
    no schools at all), so the golden path seeds them."""
    for name in ("St Agnes", "Holy Family"):
        if h.canonical.get_school_by_canonical_name(name) is None:
            h.canonical_write.create_school(canonical_name=name)
    client = _client_for(
        event_year=event_year, rows=rows if rows is not None else _ROWS
    )
    outcome = discover_scope(
        url=URL,
        client=client,
        staging=h.staging,
        ingest=h.ingest,
        correlation_id="corr-1",
    )
    adapter = select_adapter(normalize_intake_url(URL), client)
    staging_summary = stage_scope(
        outcome,
        adapter=adapter,
        staging=h.staging,
        ingest=h.ingest,
        raw_store=h.raw_store,
    )
    resolution_summary = resolve_scope(
        staging_summary,
        staging=h.staging,
        canonical=h.canonical,
        canonical_write=h.canonical_write,
        resolver=h.resolver,
        ingest=h.ingest,
        source_id=outcome.source_id,
    )
    commit_outcome = commit_scope(
        resolution_summary,
        conn=h.conn,
        db_path=h.db_path,
        source_id=outcome.source_id,
        resolver=h.resolver,
        ingest=h.ingest,
        publisher=h.publisher,
        actor="test-actor",
    )
    return outcome.ingest_run_id, commit_outcome.publication_id


def test_full_import_from_url_through_commit_and_publication(harness: Harness) -> None:
    ingest_run_id, publication_id = _run_full_import(harness)

    run = harness.ingest.get(ingest_run_id)
    assert run is not None
    assert run.state == "committed"
    assert run.result_publication_id == publication_id
    assert run.inserted_count == 2

    results = harness.conn.execute("SELECT * FROM results").fetchall()
    assert len(results) == 2
    athletes = {
        r["display_name"] for r in harness.conn.execute("SELECT * FROM athletes")
    }
    assert athletes == {"Jane Doe", "Amy Smith"}
    schools = {
        r["canonical_name"] for r in harness.conn.execute("SELECT * FROM schools")
    }
    assert schools == {"St Agnes", "Holy Family"}

    # Published snapshot is real and verifiable.
    assert (
        harness.s3.head_object(f"database/snapshots/{publication_id}/xc.db") is not None
    )


def test_frozen_season_is_refused_with_no_staging_or_canonical_change(
    harness: Harness,
) -> None:
    client = _client_for(event_year=2024, rows=_ROWS)  # 2024 is frozen
    outcome = discover_scope(
        url=URL,
        client=client,
        staging=harness.staging,
        ingest=harness.ingest,
        correlation_id="c1",
    )

    assert outcome.importable == ()
    assert len(outcome.frozen) == 1

    run = harness.ingest.get(outcome.ingest_run_id)
    assert run is not None
    assert run.state == "failed"
    assert run.error_summary is not None
    assert "frozen" in run.error_summary.lower()

    assert (
        harness.conn.execute("SELECT COUNT(*) FROM source_objects").fetchone()[0] == 0
    )
    assert (
        harness.conn.execute("SELECT COUNT(*) FROM staged_results").fetchone()[0] == 0
    )
    assert harness.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0


def test_unsupported_url_raises_before_any_ingest_run_is_created(
    harness: Harness,
) -> None:
    client = _client_for(event_year=2026, rows=_ROWS)
    with pytest.raises(UnsupportedSourceError):
        discover_scope(
            url="https://example.com/not-runsignup",
            client=client,
            staging=harness.staging,
            ingest=harness.ingest,
            correlation_id="c1",
        )
    assert harness.conn.execute("SELECT COUNT(*) FROM ingest_runs").fetchone()[0] == 0


def test_rerunning_unchanged_input_creates_no_duplicate_entities_or_results(
    harness: Harness,
) -> None:
    first_run_id, first_publication_id = _run_full_import(harness)
    second_run_id, second_publication_id = _run_full_import(harness)

    assert second_run_id != first_run_id
    second_run = harness.ingest.get(second_run_id)
    assert second_run is not None
    assert second_run.inserted_count == 0  # nothing new committed

    assert harness.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 2
    assert harness.conn.execute("SELECT COUNT(*) FROM athletes").fetchone()[0] == 2
    assert harness.conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0] == 2
    assert (
        second_publication_id != first_publication_id
    )  # still republished, just unchanged data


def test_extraction_failure_marks_the_run_failed_and_stages_nothing_for_that_item(
    harness: Harness,
) -> None:
    client = _client_for(event_year=2026, rows=_ROWS)
    outcome = discover_scope(
        url=URL,
        client=client,
        staging=harness.staging,
        ingest=harness.ingest,
        correlation_id="c1",
    )

    class _BrokenAdapter(RunSignupAdapter):
        def fetch(self, item: Any) -> Any:
            raise RuntimeError("simulated extraction failure")

    broken_adapter = _BrokenAdapter(client=client)
    with pytest.raises(RuntimeError, match="simulated extraction failure"):
        stage_scope(
            outcome,
            adapter=broken_adapter,
            staging=harness.staging,
            ingest=harness.ingest,
            raw_store=harness.raw_store,
        )

    run = harness.ingest.get(outcome.ingest_run_id)
    assert run is not None
    assert run.state == "failed"
    assert "extraction failed" in (run.error_summary or "")
    assert (
        harness.conn.execute("SELECT COUNT(*) FROM staged_results").fetchone()[0] == 0
    )


def test_commit_refuses_when_resolution_cases_are_still_pending(
    harness: Harness,
) -> None:
    # A row with no team name opens a school resolution case instead of
    # auto-resolving (see resolve_scope's no-team-name branch).
    ambiguous_row = _row(
        9001, first="No", last="Team", team="", place=1, time_text="20:00"
    )
    ambiguous_row["custom-field-100001"] = ""
    client = _client_for(event_year=2026, rows=[ambiguous_row])

    outcome = discover_scope(
        url=URL,
        client=client,
        staging=harness.staging,
        ingest=harness.ingest,
        correlation_id="c1",
    )
    adapter = select_adapter(normalize_intake_url(URL), client)
    staging_summary = stage_scope(
        outcome,
        adapter=adapter,
        staging=harness.staging,
        ingest=harness.ingest,
        raw_store=harness.raw_store,
    )
    resolution_summary = resolve_scope(
        staging_summary,
        staging=harness.staging,
        canonical=harness.canonical,
        canonical_write=harness.canonical_write,
        resolver=harness.resolver,
        ingest=harness.ingest,
        source_id=outcome.source_id,
    )
    assert resolution_summary.opened_case_count == 1

    with pytest.raises(IngestRunNotReadyError, match="pending"):
        commit_scope(
            resolution_summary,
            conn=harness.conn,
            db_path=harness.db_path,
            source_id=outcome.source_id,
            resolver=harness.resolver,
            ingest=harness.ingest,
            publisher=harness.publisher,
            actor="test-actor",
        )
    run = harness.ingest.get(outcome.ingest_run_id)
    assert run is not None
    assert run.state == "awaiting_review"  # not silently advanced
    assert harness.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0


def test_resolve_scope_opens_one_school_case_not_one_per_row(
    harness: Harness,
) -> None:
    """Regression test for the duplicate-case bug found live during Task
    18.2's production validation (2026-09-23): a real current-season meet
    with dozens of rows from the same few schools opened well over a
    hundred near-identical review cases, one per row, for the same
    handful of school decisions. Three rows sharing one raw, not-quite-
    matching team name must open exactly one case, not three."""
    harness.canonical_write.create_school(canonical_name="St Agnes School")
    rows = [
        _row(
            6001,
            first="Jane",
            last="Doe",
            team="St Agnes HS",
            place=1,
            time_text="21:30",
        ),
        _row(
            6002,
            first="Amy",
            last="Smith",
            team="St Agnes HS",
            place=2,
            time_text="22:10",
        ),
        _row(
            6003,
            first="Kim",
            last="Lee",
            team="St Agnes HS",
            place=3,
            time_text="22:40",
        ),
    ]
    client = _client_for(event_year=2026, rows=rows)

    outcome = discover_scope(
        url=URL,
        client=client,
        staging=harness.staging,
        ingest=harness.ingest,
        correlation_id="c1",
    )
    adapter = select_adapter(normalize_intake_url(URL), client)
    staging_summary = stage_scope(
        outcome,
        adapter=adapter,
        staging=harness.staging,
        ingest=harness.ingest,
        raw_store=harness.raw_store,
    )
    resolution_summary = resolve_scope(
        staging_summary,
        staging=harness.staging,
        canonical=harness.canonical,
        canonical_write=harness.canonical_write,
        resolver=harness.resolver,
        ingest=harness.ingest,
        source_id=outcome.source_id,
    )

    assert resolution_summary.opened_case_count == 3
    assert len(harness.resolver.list_pending_cases()) == 1


def test_commit_is_idempotent_on_repeat_calls(harness: Harness) -> None:
    ingest_run_id, publication_id = _run_full_import(harness)
    run = harness.ingest.get(ingest_run_id)
    assert run is not None

    resolution_summary_replay = type(
        "R", (), {"ingest_run_id": ingest_run_id, "ready": (), "opened_case_count": 0}
    )()
    outcome = commit_scope(
        resolution_summary_replay,
        conn=harness.conn,
        db_path=harness.db_path,
        source_id="unused",
        resolver=harness.resolver,
        ingest=harness.ingest,
        publisher=harness.publisher,
        actor="test-actor",
    )
    assert outcome.already_committed is True
    assert outcome.publication_id == publication_id
    assert (
        harness.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 2
    )  # unchanged


def test_publication_failure_leaves_local_commit_intact_but_marks_run_failed(
    harness: Harness,
) -> None:
    # Inject a broken publisher on a commit call so the local transaction
    # succeeds but publication fails -- proves the two are independently
    # observable (results genuinely persisted before publish ever ran).
    for name in ("St Agnes", "Holy Family"):
        harness.canonical_write.create_school(canonical_name=name)
    client = _client_for(event_year=2026, rows=_ROWS)
    outcome = discover_scope(
        url=URL,
        client=client,
        staging=harness.staging,
        ingest=harness.ingest,
        correlation_id="c2",
    )
    adapter = select_adapter(normalize_intake_url(URL), client)
    staging_summary = stage_scope(
        outcome,
        adapter=adapter,
        staging=harness.staging,
        ingest=harness.ingest,
        raw_store=harness.raw_store,
    )
    resolution_summary = resolve_scope(
        staging_summary,
        staging=harness.staging,
        canonical=harness.canonical,
        canonical_write=harness.canonical_write,
        resolver=harness.resolver,
        ingest=harness.ingest,
        source_id=outcome.source_id,
    )

    class _BrokenPublisher(SnapshotPublisher):
        def publish(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated S3 outage")

    broken_publisher = _BrokenPublisher(
        harness.s3, work_dir=harness.db_path.parent / "pub-work-2", owner_id="test"
    )

    with pytest.raises(RuntimeError, match="simulated S3 outage"):
        commit_scope(
            resolution_summary,
            conn=harness.conn,
            db_path=harness.db_path,
            source_id=outcome.source_id,
            resolver=harness.resolver,
            ingest=harness.ingest,
            publisher=broken_publisher,
            actor="test-actor",
        )

    run_after = harness.ingest.get(outcome.ingest_run_id)
    assert run_after is not None
    assert run_after.state == "failed"
    assert "committed locally" in (run_after.error_summary or "")
    # The results WERE committed locally before publish failed.
    assert harness.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 2
