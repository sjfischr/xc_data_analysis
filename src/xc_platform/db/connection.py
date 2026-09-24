"""Safe SQLite connection primitives.

Requirement 2.4 (foreign-key enforcement) and design.md section 3.1/7.2 define
two distinct connection shapes:

* :func:`open_writer_connection` -- the single local process that downloads
  the active snapshot, applies migrations, and publishes a new generation
  (design.md section 7.3). Only ``db/`` and ``cli/`` code should call this;
  adapters and agents write through the repositories in
  :mod:`xc_platform.db.repositories`, never through a raw connection
  (Requirement 10.3, Task 4.4).
* :func:`open_reader_connection` -- opens a verified, immutable local copy of
  a published snapshot read-only, matching design.md section 7.2 step 5
  (``mode=ro``, ``immutable=1``). A reader can never mutate the database file
  it opens, even if calling code has a bug.

Both factories enable ``PRAGMA foreign_keys = ON`` (Requirement 2.4: "enabled
on every connection") and return rows as :class:`sqlite3.Row` so callers can
access columns by name.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _configure_common(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def open_writer_connection(path: str | Path) -> sqlite3.Connection:
    """Open the single local writable connection to ``path``.

    ``isolation_level=None`` puts the connection in autocommit mode so that
    transaction boundaries are explicit (``BEGIN`` / ``COMMIT`` / ``ROLLBACK``
    issued by the migration runner and repositories), never implicitly
    started or committed by the ``sqlite3`` module itself.

    The file is created if it does not already exist -- this is the
    "empty-database bootstrap" entry point (Task 4.1); see
    :func:`xc_platform.db.migrator.bootstrap`.
    """
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    _configure_common(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def open_reader_connection(path: str | Path) -> sqlite3.Connection:
    """Open ``path`` read-only and immutable (design.md section 7.2 step 5).

    Callers are responsible for verifying byte size, SHA-256, and schema
    version *before* calling this (design.md section 7.2 steps 3-4) -- this
    function only enforces that, once opened, the connection cannot write.
    """
    uri = f"file:{Path(path).as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    _configure_common(conn)
    return conn
