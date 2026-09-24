"""The bounded read-only SQL escape hatch for analytics tools (Task 11.1,
design.md section 11.3's ``run_readonly_query``).

Production hardening of the pattern the Task 3.5 feasibility prototype
proved live (``feasibility/agent/agent_core.py``): a read-only immutable
connection (:func:`~xc_platform.db.connection.open_reader_connection`),
single-statement SELECT/WITH only, an SQLite authorizer denying writes,
PRAGMA, ATTACH, and extension loading as defense in depth below the text
check, a table allowlist, and row/time limits. Preferred questions use the
metric-specific tools instead; this exists only for the residual case they
don't cover.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_ROWS = 200
DEFAULT_MAX_SECONDS = 5.0

# Raw payload and audit tables are never reachable through this tool
# (design.md 11.3): an analytics agent gets aggregate/canonical data, not
# staging internals or the resolution history's evidence text.
DEFAULT_ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "meets",
        "races",
        "schools",
        "athletes",
        "athlete_seasons",
        "results",
    }
)

_WRITE_TOKENS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|begin|commit|rollback)\b",
    re.IGNORECASE,
)


class QueryRefusedError(RuntimeError):
    """Raised when a query violates a hard constraint (never executed)."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    max_rows: int
    elapsed_s: float


def _make_authorizer(
    allowed_tables: frozenset[str],
) -> Any:
    read_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}

    def authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        dbname: str | None,
        source: str | None,
    ) -> int:
        if action in read_actions:
            if action == sqlite3.SQLITE_READ and arg1 and arg1 not in allowed_tables:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorizer


def run_readonly_query(
    connection: sqlite3.Connection,
    sql: str,
    *,
    allowed_tables: frozenset[str] = DEFAULT_ALLOWED_TABLES,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> QueryResult:
    """Run exactly one bounded read-only statement. Raises
    :class:`QueryRefusedError` instead of executing anything that fails a
    check -- callers (the ``@tool``-wrapped adapter) turn that into a
    refusal result for the agent, never an exception that aborts the
    conversation."""
    statement = sql.strip().rstrip(";")
    if ";" in statement:
        raise QueryRefusedError("only a single statement is permitted")
    if not re.match(r"^\s*(select|with)\b", statement, re.IGNORECASE):
        raise QueryRefusedError("only SELECT or WITH statements are permitted")
    if _WRITE_TOKENS.search(statement):
        raise QueryRefusedError("statement contains a non-read keyword")

    connection.set_authorizer(_make_authorizer(allowed_tables))
    started = time.monotonic()

    def interrupt_if_slow() -> int:
        return 1 if (time.monotonic() - started) > max_seconds else 0

    connection.set_progress_handler(interrupt_if_slow, 10_000)
    try:
        cursor = connection.execute(statement)
        rows = cursor.fetchmany(max_rows)
        columns = [d[0] for d in (cursor.description or [])]
        truncated = len(cursor.fetchmany(1)) > 0
    except sqlite3.DatabaseError as error:
        raise QueryRefusedError(f"query refused by SQLite: {error}") from error
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)

    return QueryResult(
        columns=columns,
        rows=[list(row) for row in rows],
        row_count=len(rows),
        truncated=truncated,
        max_rows=max_rows,
        elapsed_s=round(time.monotonic() - started, 4),
    )
