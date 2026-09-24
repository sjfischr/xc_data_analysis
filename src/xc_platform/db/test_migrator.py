"""Tests for the migration framework (Task 4.1).

Requirements exercised: R2.4 (foreign keys on every connection), R2.5
(ordered migrations, empty-database bootstrap, incompatible-version
detection).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.errors import IncompatibleSchemaError, PartiallyAppliedSchemaError
from xc_platform.db.migrator import (
    bootstrap,
    default_migrations_dir,
    latest_schema_version,
    load_migrations,
    migrate,
)

EXPECTED_TABLES = {
    "schema_migrations",
    "data_sources",
    "db_publications",
    "ingest_runs",
    "source_objects",
    "staged_results",
    "meets",
    "races",
    "schools",
    "school_aliases",
    "athletes",
    "athlete_aliases",
    "athlete_seasons",
    "results",
    "source_entity_links",
    "award_rules",
    "metric_versions",
    "resolution_cases",
    "resolution_decisions",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_real_migrations_directory_loads_as_contiguous_sequence() -> None:
    migrations = load_migrations()
    versions = [m.version for m in migrations]
    assert versions == list(range(1, len(migrations) + 1))
    assert latest_schema_version() == versions[-1]


def test_bootstrap_creates_every_table_on_an_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "xc.db"
    conn = bootstrap(db_path)
    try:
        assert EXPECTED_TABLES.issubset(_table_names(conn))
        applied = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in applied] == list(range(1, len(applied) + 1))
    finally:
        conn.close()


def test_foreign_keys_are_enforced_on_the_bootstrapped_connection(
    tmp_path: Path,
) -> None:
    conn = bootstrap(tmp_path / "xc.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO races (race_id, meet_id, division_code, "
                "gender_code, created_at, updated_at) "
                "VALUES ('r1', 'does-not-exist', 'varsity', 'M', 't', 't')"
            )
    finally:
        conn.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "xc.db"
    conn = bootstrap(db_path)
    try:
        second_run = migrate(conn)
        assert second_run == []
    finally:
        conn.close()


def test_migrate_rejects_a_gap_in_applied_history(tmp_path: Path) -> None:
    db_path = tmp_path / "xc.db"
    conn = open_writer_connection(db_path)
    try:
        migrate(conn)
        # Simulate a corrupted tracking table where an earlier version's
        # record is missing but a later one's survives -- the DDL for both
        # migrations has already run, so only the bookkeeping is damaged.
        conn.execute("DELETE FROM schema_migrations WHERE version = 1")
        with pytest.raises(PartiallyAppliedSchemaError):
            migrate(conn)
    finally:
        conn.close()


def test_migrate_rejects_a_future_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "xc.db"
    conn = open_writer_connection(db_path)
    try:
        migrate(conn)
        # Contiguous with what's already applied (no gap), but beyond any
        # migration file this code knows about -- as if a newer code version
        # had migrated this database and an older version reopened it.
        next_version = latest_schema_version() + 1
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            "VALUES (?, 'from_the_future', 'deadbeef', 'now')",
            (next_version,),
        )
        with pytest.raises(IncompatibleSchemaError):
            migrate(conn)
    finally:
        conn.close()


def test_migrate_rejects_checksum_drift(tmp_path: Path) -> None:
    db_path = tmp_path / "xc.db"
    conn = open_writer_connection(db_path)
    try:
        migrate(conn)
        conn.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1"
        )
        with pytest.raises(IncompatibleSchemaError):
            migrate(conn)
    finally:
        conn.close()


def test_load_migrations_rejects_a_gap_on_disk(tmp_path: Path) -> None:
    bad_dir = tmp_path / "migrations"
    bad_dir.mkdir()
    (bad_dir / "0001_first.sql").write_text("CREATE TABLE a (id TEXT);")
    (bad_dir / "0003_third.sql").write_text("CREATE TABLE c (id TEXT);")
    with pytest.raises(ValueError, match="contiguous"):
        load_migrations(bad_dir)


def test_default_migrations_dir_points_at_repo_root_migrations() -> None:
    directory = default_migrations_dir()
    assert directory.name == "migrations"
    assert (directory / "0001_source_and_ingest.sql").is_file()
