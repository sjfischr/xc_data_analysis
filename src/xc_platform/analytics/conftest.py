from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_writer_connection(tmp_path / "xc.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()
