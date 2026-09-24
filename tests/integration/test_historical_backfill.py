"""End-to-end proof of Task 5.1-5.4 against the real frozen baseline.

Runs the actual historical backfill against the real
``data/merged/season_results.csv`` and ``name_corrections.csv`` -- not a
synthetic fixture -- and checks the result against the real frozen-baseline
parity fixtures in ``tests/fixtures/baseline/``. This is the test that
proves the migration actually reproduces the historical calculations, not
just that the code runs on made-up data.

Needs no external service or local-only resource beyond files already
committed to the repository, so it is not marked ``integration`` (that
marker is for tests needing resources unavailable in ordinary CI).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.migration.aliases import seed_name_corrections, seed_school_aliases
from xc_platform.migration.backfill import (
    FrozenBaselineChangedError,
    run_backfill,
)
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter
from xc_platform.migration.parity import generate_reconciliation_report

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_CSV = REPO_ROOT / "data" / "merged" / "season_results.csv"
CORRECTIONS_CSV = REPO_ROOT / "name_corrections.csv"
BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "baseline"


def _expected_sha256() -> str:
    manifest = json.loads((BASELINE_DIR / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest["canonical_dataset"]["sha256"])


@pytest.fixture(scope="module")
def migrated_conn(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path_factory.mktemp("historical") / "xc.db"
    conn = open_writer_connection(db_path)
    migrate(conn)
    run_backfill(
        conn,
        CANONICAL_CSV,
        correlation_id="test-real-backfill",
        expected_sha256=_expected_sha256(),
    )
    writer = HistoricalCanonicalWriter(conn)
    source_id = conn.execute(
        "SELECT source_id FROM data_sources WHERE source_namespace = "
        "'historical_csv:season_results'"
    ).fetchone()["source_id"]
    seed_school_aliases(writer, source_id=source_id)
    seed_name_corrections(writer, CORRECTIONS_CSV, source_id=source_id)
    try:
        yield conn
    finally:
        conn.close()


def test_frozen_baseline_still_matches_its_recorded_fingerprint() -> None:
    """If this fails, the frozen CSV changed and must be re-frozen deliberately."""
    from xc_platform.migration.backfill import sha256_of_file

    assert sha256_of_file(CANONICAL_CSV) == _expected_sha256()


def test_total_row_count_matches_the_baseline(
    migrated_conn: sqlite3.Connection,
) -> None:
    total_staged = migrated_conn.execute(
        "SELECT COUNT(*) AS n FROM staged_results"
    ).fetchone()["n"]
    assert total_staged == 4204


def test_exactly_one_row_is_quarantined_the_documented_2023_meet2_gap(
    migrated_conn: sqlite3.Connection,
) -> None:
    quarantined = migrated_conn.execute(
        "SELECT validation_warnings_json FROM staged_results "
        "WHERE validation_state = 'quarantined'"
    ).fetchall()
    assert len(quarantined) == 1
    assert "2023" in quarantined[0]["validation_warnings_json"]
    assert "1.10" in quarantined[0]["validation_warnings_json"]


def test_unique_athlete_and_school_counts_match_baseline_exactly(
    migrated_conn: sqlite3.Connection,
) -> None:
    baseline = json.loads((BASELINE_DIR / "counts.json").read_text(encoding="utf-8"))
    athletes = migrated_conn.execute("SELECT COUNT(*) AS n FROM athletes").fetchone()[
        "n"
    ]
    schools = migrated_conn.execute(
        "SELECT COUNT(*) AS n FROM schools WHERE canonical_name != 'Unknown'"
    ).fetchone()["n"]
    assert athletes == baseline["unique_athletes"]
    assert schools == baseline["unique_teams"]


def test_full_reconciliation_report_has_zero_unexplained_discrepancies(
    migrated_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    report = generate_reconciliation_report(
        migrated_conn, BASELINE_DIR, report_path=tmp_path / "reconciliation.json"
    )
    assert report.checks_run == [
        "counts",
        "team_scores",
        "saint_sebastian",
        "athlete_histories",
        "normalized_metrics",
    ]
    assert report.discrepancies == []
    assert report.passed


def test_parity_fails_when_a_baseline_fixture_is_intentionally_mutated(
    migrated_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """Task 5.4: prove the parity check itself can actually fail."""
    mutated_dir = tmp_path / "mutated_baseline"
    mutated_dir.mkdir()
    for name in (
        "manifest.json",
        "counts.json",
        "team_scores.json",
        "saint_sebastian.json",
        "athlete_histories.json",
        "normalized_metrics.json",
    ):
        (mutated_dir / name).write_text(
            (BASELINE_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )

    counts = json.loads((mutated_dir / "counts.json").read_text(encoding="utf-8"))
    counts["unique_athletes"] += 1
    (mutated_dir / "counts.json").write_text(json.dumps(counts), encoding="utf-8")

    report = generate_reconciliation_report(
        migrated_conn, mutated_dir, report_path=tmp_path / "mutated_reconciliation.json"
    )
    assert not report.passed
    assert any(f.check == "counts.unique_athletes" for f in report.discrepancies)


def test_frozen_baseline_changed_error_is_raised_on_hash_mismatch(
    tmp_path: Path,
) -> None:
    conn = open_writer_connection(tmp_path / "xc.db")
    migrate(conn)
    try:
        with pytest.raises(FrozenBaselineChangedError):
            run_backfill(
                conn,
                CANONICAL_CSV,
                correlation_id="test-hash-mismatch",
                expected_sha256="0" * 64,
            )
    finally:
        conn.close()
