"""Shared transaction boundary for all repositories (Requirement 2.7)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class BaseRepository:
    """Owns one connection and provides an explicit transaction boundary.

    Every write method a subclass exposes should wrap its statements in
    :meth:`transaction`. A failure anywhere inside the block rolls back
    every change made since ``BEGIN`` (Requirement 2.7); callers never see a
    partially applied write.

    Repository write methods are not designed to be composed by nesting
    ``transaction()`` calls on the same connection -- SQLite raises on a
    ``BEGIN`` while a transaction is already open. A caller that needs
    several repositories' writes to commit or roll back together should be
    added as its own orchestration method once that need exists, rather than
    nesting these context managers.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection, for read-only ad hoc queries.

        Exists so callers that need a plain ``SELECT`` this repository
        doesn't expose as a named method (or that compose more than one
        repository against the same connection, such as the historical
        backfill in :mod:`xc_platform.migration`) don't have to reach for
        the "private" ``_conn`` attribute to get it.
        """
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
