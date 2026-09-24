"""Bounded read-only snapshot tool for the AgentCore feasibility agent.

Mirrors feasibility/agent/agent_core.py so the same constraints are proven in
the deployed runtime, not just locally:

* the snapshot is verified by SHA-256 before it is opened;
* it is opened ``mode=ro&immutable=1``;
* a SQLite authorizer denies everything except reads of allowlisted tables;
* one statement only, with row and wall-clock limits.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

SNAPSHOT_PATH = Path(__file__).parent / "data" / "xc-snapshot.db"
EXPECTED_SHA256 = os.environ.get("XC_SNAPSHOT_SHA256", "")
PUBLICATION_ID = os.environ.get("XC_PUBLICATION_ID", "feasibility-pub-001")

ALLOWED_TABLES: frozenset[str] = frozenset({"results"})
MAX_ROWS = 200
MAX_QUERY_SECONDS = 5.0

_WRITE_TOKENS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|begin|commit|rollback)\b",
    re.IGNORECASE,
)


class ToolRefusal(RuntimeError):
    """Raised when a tool call violates a hard constraint."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_and_open() -> tuple[sqlite3.Connection, dict[str, Any]]:
    """Verify the bundled snapshot, then open it read-only and immutable."""
    if not SNAPSHOT_PATH.exists():
        raise RuntimeError(f"snapshot missing at {SNAPSHOT_PATH}")
    actual = _sha256(SNAPSHOT_PATH)
    verified = (not EXPECTED_SHA256) or actual == EXPECTED_SHA256
    if EXPECTED_SHA256 and not verified:
        # Fail closed: a snapshot that does not match its manifest digest is
        # never opened, regardless of what SQLite thinks of its structure.
        raise RuntimeError("snapshot SHA-256 does not match the expected digest")
    uri = f"file:{SNAPSHOT_PATH.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    return connection, {
        "sha256": actual,
        "sha256_verified": bool(EXPECTED_SHA256) and verified,
        "integrity_check": integrity,
        "byte_size": SNAPSHOT_PATH.stat().st_size,
        "publication_id": PUBLICATION_ID,
    }


def _authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    dbname: str | None,
    source: str | None,
) -> int:
    read_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if action in read_actions:
        if action == sqlite3.SQLITE_READ and arg1 and arg1 not in ALLOWED_TABLES:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_readonly_query(connection: sqlite3.Connection, sql: str) -> dict[str, Any]:
    statement = sql.strip().rstrip(";")
    if ";" in statement:
        raise ToolRefusal("only a single statement is permitted")
    if not re.match(r"^\s*(select|with)\b", statement, re.IGNORECASE):
        raise ToolRefusal("only SELECT or WITH statements are permitted")
    if _WRITE_TOKENS.search(statement):
        raise ToolRefusal("statement contains a non-read keyword")

    connection.set_authorizer(_authorizer)
    started = time.monotonic()

    def interrupt_if_slow() -> int:
        return 1 if (time.monotonic() - started) > MAX_QUERY_SECONDS else 0

    connection.set_progress_handler(interrupt_if_slow, 10_000)
    try:
        cursor = connection.execute(statement)
        rows = cursor.fetchmany(MAX_ROWS)
        columns = [d[0] for d in (cursor.description or [])]
        truncated = len(cursor.fetchmany(1)) > 0
    except sqlite3.DatabaseError as error:
        raise ToolRefusal(f"query refused by SQLite: {error}") from error
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)

    return {
        "columns": columns,
        "rows": [list(row) for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_s": round(time.monotonic() - started, 4),
        "publication_id": PUBLICATION_ID,
    }
