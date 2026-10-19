from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from xc_platform.analytics.query import QueryRefusedError, run_readonly_query
from xc_platform.db.connection import open_reader_connection
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository


def test_select_returns_bounded_rows(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    write.create_school(canonical_name="St Agnes")
    write.create_school(canonical_name="Holy Family")

    result = run_readonly_query(
        conn, "SELECT canonical_name FROM schools ORDER BY canonical_name"
    )
    assert result.row_count == 2
    assert result.rows == [["Holy Family"], ["St Agnes"]]
    assert not result.truncated


def test_result_set_is_truncated_at_max_rows(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    for i in range(5):
        write.create_school(canonical_name=f"School {i}")

    result = run_readonly_query(conn, "SELECT canonical_name FROM schools", max_rows=2)
    assert result.row_count == 2
    assert result.truncated is True


def test_a_second_statement_is_refused() -> None:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(QueryRefusedError, match="single statement"):
        run_readonly_query(conn, "SELECT 1; DROP TABLE schools")


def test_a_write_statement_is_refused() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schools (id INTEGER)")
    with pytest.raises(QueryRefusedError):
        run_readonly_query(conn, "DELETE FROM schools")


def test_a_write_keyword_disguised_as_a_trailing_comment_is_refused() -> None:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(QueryRefusedError, match="non-read keyword"):
        run_readonly_query(conn, "SELECT 1 -- then delete from schools")


def test_a_table_outside_the_allowlist_is_denied_by_the_authorizer(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "INSERT INTO ingest_runs (ingest_run_id, source_id, submitted_url, "
        "adapter_type, correlation_id, state, started_at) "
        "SELECT 'x', source_id, 'u', 'runsignup', 'c', 'pending', '2026-01-01' "
        "FROM data_sources LIMIT 0"
    )
    with pytest.raises(QueryRefusedError):
        run_readonly_query(conn, "SELECT * FROM ingest_runs")


def test_pragma_is_refused_even_though_it_starts_with_neither_select_nor_write_token(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(QueryRefusedError, match="only SELECT or WITH"):
        run_readonly_query(conn, "PRAGMA table_info(schools)")


def test_runs_against_a_real_immutable_reader_connection(tmp_path: Path) -> None:
    """The authorizer/statement checks are the primary guard, but this tool
    is meant to run over :func:`open_reader_connection` -- prove a SELECT
    still works there, on a real file, not just an in-memory writer conn."""
    from xc_platform.db.connection import open_writer_connection
    from xc_platform.db.migrator import migrate

    db_path = tmp_path / "snapshot.db"
    writer = open_writer_connection(db_path)
    migrate(writer)
    CanonicalWriteRepository(writer).create_school(canonical_name="St Agnes")
    writer.close()

    reader = open_reader_connection(db_path)
    try:
        result = run_readonly_query(reader, "SELECT canonical_name FROM schools")
        assert result.rows == [["St Agnes"]]
    finally:
        reader.close()


def test_concurrent_queries_on_one_connection_do_not_deadlock(tmp_path: Path) -> None:
    """Found 2026-10-19: two threads querying one shared connection froze
    the whole process (each holds what the other needs: the connection
    mutex and the GIL). faulthandler's watchdog runs without the GIL, so a
    regression fails loudly here instead of hanging CI forever."""
    import faulthandler
    import threading

    path = tmp_path / "shared.db"
    setup = sqlite3.connect(path)
    setup.execute("CREATE TABLE results (x INTEGER)")
    setup.executemany("INSERT INTO results VALUES (?)", [(i,) for i in range(2000)])
    setup.commit()
    setup.close()
    shared = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    heavy = "SELECT count(*) FROM results a, results b WHERE a.x + b.x > 17"
    answers: list[list[list[object]]] = []

    def query() -> None:
        answers.append(run_readonly_query(shared, heavy, max_seconds=30).rows)

    faulthandler.dump_traceback_later(60, exit=True)
    try:
        threads = [threading.Thread(target=query) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        faulthandler.cancel_dump_traceback_later()
        shared.close()
    assert answers == [[[3_999_829]]] * 3


def test_race_day_weather_is_readable(conn: sqlite3.Connection) -> None:
    """The weather table and its race-start view are open to the agent."""
    for sql in (
        "SELECT temperature_f FROM meet_weather_hourly",
        "SELECT race_id, temperature_f, weather_description FROM v_race_start_weather",
    ):
        assert run_readonly_query(conn, sql).row_count == 0
