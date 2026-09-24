"""Writer hydration tests (Task 19.0): the production writer starts from
the active published snapshot, not an empty database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from xc_platform.db.publication.hydration import hydrate_writer_database
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import FakeS3Client
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.publications import PublicationRepository


def _add_marker_row(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO schools (school_id, canonical_name, display_name, status, "
        "created_at, updated_at) VALUES ('s1', 'st-marker', 'St Marker', "
        "'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def test_hydrates_from_the_active_snapshot_and_records_it_as_active(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    _add_marker_row(sample_db)
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    manifest = publisher.publish(
        sample_db, created_by="seed", ingest_run_id="seed", summary={"inserted": 1}
    )

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")
    conn, seeded_from = hydrate_writer_database(reader, tmp_path / "writer.db")
    try:
        assert seeded_from is not None
        assert seeded_from.publication_id == manifest.publication_id
        assert conn.execute("SELECT display_name FROM schools").fetchone()[0] == (
            "St Marker"
        )
        active = PublicationRepository(conn).get_active()
        assert active is not None
        assert active.publication_id == manifest.publication_id
    finally:
        conn.close()


def test_next_publish_from_a_hydrated_writer_wins_the_compare_and_swap(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    """The regression: before hydration, the writer had no active
    publication row, so a commit published as a bootstrap and was refused."""
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    first = publisher.publish(
        sample_db, created_by="seed", ingest_run_id="seed", summary={}
    )

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")
    writer_path = tmp_path / "writer.db"
    conn, _ = hydrate_writer_database(reader, writer_path)
    active = PublicationRepository(conn).get_active()
    assert active is not None
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    second = publisher.publish(
        writer_path,
        created_by="run-2",
        ingest_run_id="run-2",
        summary={},
        parent_publication_id=active.publication_id,
    )
    assert second.parent_publication_id == first.publication_id


def test_rehydrating_the_same_generation_is_idempotent(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    publisher.publish(sample_db, created_by="seed", ingest_run_id="seed", summary={})
    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")

    for _ in range(2):
        conn, _ = hydrate_writer_database(reader, tmp_path / "writer.db")
        count = conn.execute(
            "SELECT COUNT(*) FROM db_publications WHERE status = 'active'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


def test_nothing_published_yet_starts_an_empty_migrated_writer(
    fake_s3: FakeS3Client, tmp_path: Path
) -> None:
    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")
    conn, seeded_from = hydrate_writer_database(reader, tmp_path / "writer.db")
    try:
        assert seeded_from is None
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1
        assert PublicationRepository(conn).get_active() is None
    finally:
        conn.close()
