"""Proves the full local/test runtime path end to end (Task 11.5): publish
a real snapshot to a fake S3, resolve+verify+pin it through the same
:class:`SnapshotReader` production code uses, build an agent bound to it,
and run a real tool-calling turn -- all without any network call or AWS
credential.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xc_platform.agents._fake_model import FakeModel, TextTurn, ToolCallTurn
from xc_platform.agents.runtime import analytics_session
from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import FakeS3Client
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository


@pytest.fixture
def fake_s3() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def published_snapshot_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "source.db"
    conn = open_writer_connection(db_path)
    migrate(conn)
    CanonicalWriteRepository(conn).create_school(canonical_name="St Agnes")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return db_path


def test_analytics_session_resolves_a_real_published_snapshot_and_answers(
    fake_s3: FakeS3Client, published_snapshot_path: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    manifest = publisher.publish(
        published_snapshot_path,
        created_by="run-1",
        ingest_run_id="run-1",
        summary={"results": 0},
    )

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")
    fake_model = FakeModel(
        script=[
            ToolCallTurn(tool_name="list_dimensions_tool", tool_input={}),
            TextTurn(
                text=f"1 school on record (publication {manifest.publication_id})."
            ),
        ]
    )

    with analytics_session(reader, model=fake_model) as session:
        assert session.publication_id == manifest.publication_id
        result = session.agent("How many schools are there?")
        assert manifest.publication_id in str(result)

    # The connection is closed on exit -- further use raises.
    with pytest.raises(Exception):  # noqa: B017 -- sqlite3.ProgrammingError, exact type is an implementation detail
        session.connection.execute("SELECT 1")
