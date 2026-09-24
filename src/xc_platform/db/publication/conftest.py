from __future__ import annotations

from pathlib import Path

import pytest

from xc_platform.db.migrator import bootstrap
from xc_platform.db.publication.s3_client import FakeS3Client


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    """A real, migrated, valid local SQLite database file."""
    db_path = tmp_path / "sample.db"
    conn = bootstrap(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return db_path


@pytest.fixture
def fake_s3() -> FakeS3Client:
    return FakeS3Client()
