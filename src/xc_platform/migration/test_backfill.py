from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.migration.backfill import (
    FrozenBaselineChangedError,
    run_backfill,
    sha256_of_file,
)

FIELDS = [
    "season_year",
    "meet_number",
    "meet_series",
    "meet_name",
    "division",
    "gender",
    "athlete_full_name",
    "team_name",
    "bib",
    "grade",
    "place_overall",
    "finish_time_s",
    "finish_time_str",
    "Scored",
]


def _row(**overrides: str) -> dict[str, str]:
    base = {
        "season_year": "2024",
        "meet_number": "1",
        "meet_series": "NVJCYO Cross Country Developmental",
        "meet_name": "NVJCYO Cross Country Developmental Meet 1",
        "division": "Frosh",
        "gender": "Boys",
        "athlete_full_name": "",
        "team_name": "St Agnes",
        "bib": "",
        "grade": "4",
        "place_overall": "",
        "finish_time_s": "",
        "finish_time_str": "",
        "Scored": "",
    }
    base.update(overrides)
    return base


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_writer_connection(tmp_path / "xc.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def _sample_rows() -> list[dict[str, str]]:
    rows = []
    # 6 St Agnes Frosh Boys finishers: 5 scoring, 1 non-scoring (place 6).
    for place, (name, time_s) in enumerate(
        [
            ("Runner One", "500"),
            ("Runner Two", "510"),
            ("Runner Three", "520"),
            ("Runner Four", "530"),
            ("Runner Five", "540"),
            ("Runner Six", "550"),
        ],
        start=1,
    ):
        rows.append(
            _row(
                athlete_full_name=name,
                place_overall=str(place),
                finish_time_s=time_s,
                finish_time_str=f"{int(time_s) // 60}:{int(time_s) % 60:02d}",
            )
        )
    # A rival team with only 4 finishers: never scores.
    for place, name in enumerate(
        ["Rival One", "Rival Two", "Rival Three", "Rival Four"], start=7
    ):
        rows.append(
            _row(
                athlete_full_name=name,
                team_name="Holy Spirit",
                place_overall=str(place),
                finish_time_s=str(600 + place),
                finish_time_str="10:00",
            )
        )
    # A result with no team name at all (Requirement 1.7 gap).
    rows.append(
        _row(
            athlete_full_name="No Team Runner",
            team_name="",
            place_overall="11",
            finish_time_s="700",
            finish_time_str="11:40",
        )
    )
    # The documented team-only artifact: no athlete name at all.
    rows.append(_row(athlete_full_name="", place_overall="1"))
    return rows


def test_backfill_promotes_valid_rows_and_quarantines_the_team_only_row(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    csv_path = tmp_path / "season_results.csv"
    _write_csv(csv_path, _sample_rows())

    report = run_backfill(conn, csv_path, correlation_id="test-corr")

    assert report.total_rows == 12
    assert report.inserted_results == 11
    assert len(report.quarantined) == 1
    assert "team-only" in report.quarantined[0].reason

    result_count = conn.execute("SELECT COUNT(*) AS n FROM results").fetchone()["n"]
    assert result_count == 11

    unknown_school = conn.execute(
        "SELECT school_id FROM schools WHERE canonical_name = 'Unknown'"
    ).fetchone()
    assert unknown_school is not None
    no_team_result = conn.execute(
        "SELECT school_id FROM results r JOIN athletes a ON a.athlete_id = "
        "r.athlete_id WHERE a.display_name = 'No Team Runner'"
    ).fetchone()
    assert no_team_result["school_id"] == unknown_school["school_id"]


def test_backfill_ingest_run_reaches_awaiting_review(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    csv_path = tmp_path / "season_results.csv"
    _write_csv(csv_path, _sample_rows())
    report = run_backfill(conn, csv_path, correlation_id="test-corr")

    run = conn.execute(
        "SELECT state, inserted_count, quarantined_count FROM ingest_runs "
        "WHERE ingest_run_id = ?",
        (report.ingest_run_id,),
    ).fetchone()
    assert run["state"] == "awaiting_review"
    assert run["inserted_count"] == 11
    assert run["quarantined_count"] == 1


def test_team_score_view_scores_exactly_the_qualifying_team(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    csv_path = tmp_path / "season_results.csv"
    _write_csv(csv_path, _sample_rows())
    run_backfill(conn, csv_path, correlation_id="test-corr")

    scores = conn.execute(
        "SELECT vts.score, vts.scoring_runners, sc.canonical_name "
        "FROM v_team_scores vts JOIN schools sc ON sc.school_id = vts.school_id"
    ).fetchall()
    # St Agnes: 5 finishers scored (places 1-5 = 15), the 6th never counted.
    # Holy Spirit (4 finishers) and the Unknown placeholder never qualify.
    assert len(scores) == 1
    assert scores[0]["canonical_name"] == "St Agnes"
    assert scores[0]["score"] == 15
    assert scores[0]["scoring_runners"] == 5


def test_saint_sebastian_requires_running_every_meet_that_season(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    rows = [
        _row(
            meet_number="1",
            athlete_full_name="Always Runs",
            place_overall="1",
            finish_time_s="500",
        ),
        _row(
            meet_number="2",
            meet_name="NVJCYO Cross Country Developmental Meet 2",
            athlete_full_name="Always Runs",
            place_overall="1",
            finish_time_s="490",
        ),
        _row(
            meet_number="1",
            athlete_full_name="Misses One Meet",
            place_overall="2",
            finish_time_s="510",
        ),
        # No meet 2 row for "Misses One Meet" -- ineligible for the season.
    ]
    csv_path = tmp_path / "season_results.csv"
    _write_csv(csv_path, rows)
    run_backfill(conn, csv_path, correlation_id="test-corr")

    standings = conn.execute(
        "SELECT athlete_display_name, standing_rank, required_meets, meets_run "
        "FROM v_saint_sebastian"
    ).fetchall()
    names = {row["athlete_display_name"] for row in standings}
    assert names == {"Always Runs"}
    assert standings[0]["required_meets"] == 2
    assert standings[0]["meets_run"] == 2
    assert standings[0]["standing_rank"] == 1


def test_frozen_baseline_hash_mismatch_is_refused(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    csv_path = tmp_path / "season_results.csv"
    _write_csv(csv_path, _sample_rows())
    with pytest.raises(FrozenBaselineChangedError):
        run_backfill(
            conn,
            csv_path,
            correlation_id="test-corr",
            expected_sha256="0" * 64,
        )


def test_sha256_of_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("hello world", encoding="utf-8")
    import hashlib

    assert sha256_of_file(path) == hashlib.sha256(b"hello world").hexdigest()
