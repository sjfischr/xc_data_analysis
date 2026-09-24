from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.aliases import (
    LEGACY_TEAM_NAME_MAPPING,
    normalize_alias_value,
    seed_name_corrections,
    seed_school_aliases,
)
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_writer_connection(tmp_path / "xc.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def source_id(conn: sqlite3.Connection) -> str:
    return StagingRepository(conn).get_or_create_source(
        source_namespace="historical_csv:season_results",
        adapter_type="historical_csv",
        base_domain="local",
    )


def test_normalize_alias_value_folds_case_and_whitespace() -> None:
    assert normalize_alias_value("  St.  Agnes   Parish ") == "st. agnes parish"


def test_seed_school_aliases_creates_one_alias_per_mapping(
    conn: sqlite3.Connection, source_id: str
) -> None:
    # Two raw spellings ("St. John The/the Evangelist Parish") differ only in
    # case and fold to the same normalized_value, so they share one alias
    # row -- the count is over distinct normalized values, not dict entries.
    distinct_normalized = {normalize_alias_value(k) for k in LEGACY_TEAM_NAME_MAPPING}

    writer = HistoricalCanonicalWriter(conn)
    created = seed_school_aliases(writer, source_id=source_id)
    assert created == len(distinct_normalized)

    row = conn.execute(
        "SELECT sc.canonical_name FROM school_aliases al "
        "JOIN schools sc ON sc.school_id = al.school_id "
        "WHERE al.raw_value = 'St. Agnes Parish'"
    ).fetchone()
    assert row["canonical_name"] == "St Agnes"


def test_seed_school_aliases_is_idempotent(
    conn: sqlite3.Connection, source_id: str
) -> None:
    writer = HistoricalCanonicalWriter(conn)
    seed_school_aliases(writer, source_id=source_id)
    second = seed_school_aliases(writer, source_id=source_id)
    assert second == 0
    distinct_normalized = {normalize_alias_value(k) for k in LEGACY_TEAM_NAME_MAPPING}
    count = conn.execute("SELECT COUNT(*) AS n FROM school_aliases").fetchone()["n"]
    assert count == len(distinct_normalized)


def test_seed_name_corrections_apply_review_keep(
    conn: sqlite3.Connection, source_id: str, tmp_path: Path
) -> None:
    csv_path = tmp_path / "name_corrections.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer_csv = csv.writer(handle)
        writer_csv.writerow(
            ["original_name", "corrected_name", "team", "action", "notes"]
        )
        writer_csv.writerow(
            ["Gwen Fischer", "Gwendolyn Fischer", "St Agnes", "apply", "Nickname"]
        )
        writer_csv.writerow(
            ["RJ Johnson", "RJ Johnson", "St Agnes", "keep", "Keep as RJ"]
        )
        writer_csv.writerow(
            [
                "Liam Niez",
                "William Niez",
                "St Agnes",
                "review",
                "Could be different person",
            ]
        )

    writer = HistoricalCanonicalWriter(conn)
    summary = seed_name_corrections(writer, csv_path, source_id=source_id)

    assert summary.applied_aliases == 1
    assert summary.keep_decisions == 1
    assert summary.review_cases == 1

    alias = conn.execute(
        "SELECT a.display_name FROM athlete_aliases al "
        "JOIN athletes a ON a.athlete_id = al.athlete_id "
        "WHERE al.raw_value = 'Gwen Fischer'"
    ).fetchone()
    assert alias["display_name"] == "Gwendolyn Fischer"

    # "keep" produced an already-decided case, not an alias.
    kept_alias = conn.execute(
        "SELECT COUNT(*) AS n FROM athlete_aliases WHERE raw_value = 'RJ Johnson'"
    ).fetchone()
    assert kept_alias["n"] == 0
    kept_case = conn.execute(
        "SELECT status FROM resolution_cases WHERE evidence_json LIKE '%RJ Johnson%'"
    ).fetchone()
    assert kept_case["status"] == "rejected"

    # "review" opened a pending case and created no alias.
    review_alias = conn.execute(
        "SELECT COUNT(*) AS n FROM athlete_aliases WHERE raw_value = 'Liam Niez'"
    ).fetchone()
    assert review_alias["n"] == 0
    review_case = conn.execute(
        "SELECT status FROM resolution_cases WHERE evidence_json LIKE '%Liam Niez%'"
    ).fetchone()
    assert review_case["status"] == "pending"
