"""Tests for connection primitives (Task 4.1, Requirement 2.4)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from xc_platform.db.connection import open_reader_connection, open_writer_connection
from xc_platform.db.migrator import migrate


def test_writer_connection_enables_foreign_keys(tmp_path: Path) -> None:
    conn = open_writer_connection(tmp_path / "xc.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_writer_connection_creates_a_new_file(tmp_path: Path) -> None:
    db_path = tmp_path / "new.db"
    assert not db_path.exists()
    conn = open_writer_connection(db_path)
    conn.execute("CREATE TABLE t (id TEXT)")
    conn.close()
    assert db_path.exists()


def test_reader_connection_enables_foreign_keys_and_is_read_only(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "xc.db"
    writer = open_writer_connection(db_path)
    migrate(writer)
    writer.close()

    reader = open_reader_connection(db_path)
    try:
        assert reader.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.execute(
                "INSERT INTO data_sources (source_id, source_namespace, "
                "adapter_type, base_domain, created_at) "
                "VALUES ('s1', 'ns', 'runsignup', 'runsignup.com', 't')"
            )
    finally:
        reader.close()


def test_reader_connection_can_query_rows_written_by_the_writer(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "xc.db"
    writer = open_writer_connection(db_path)
    migrate(writer)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "INSERT INTO data_sources (source_id, source_namespace, adapter_type, "
        "base_domain, created_at) VALUES ('s1', 'runsignup', 'runsignup', "
        "'runsignup.com', 't')"
    )
    writer.execute("COMMIT")
    writer.close()

    reader = open_reader_connection(db_path)
    try:
        row = reader.execute(
            "SELECT source_namespace FROM data_sources WHERE source_id = 's1'"
        ).fetchone()
        assert row["source_namespace"] == "runsignup"
    finally:
        reader.close()
