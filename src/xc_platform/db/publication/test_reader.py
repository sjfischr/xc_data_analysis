"""Reader tests (Task 6.2, 6.5): refresh, caching, corrupt downloads, and
"a failed refresh never evicts the previous valid snapshot."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xc_platform.db.publication import layout
from xc_platform.db.publication.errors import (
    ActiveManifestMissingError,
    SnapshotVerificationError,
)
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import FakeS3Client, _FakeObject
from xc_platform.db.publication.writer import SnapshotPublisher


def test_current_raises_when_nothing_has_ever_been_published(
    fake_s3: FakeS3Client, tmp_path: Path
) -> None:
    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")
    with pytest.raises(ActiveManifestMissingError):
        reader.current()


def test_current_downloads_verifies_and_opens_the_active_snapshot(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    manifest = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"results": 4203}
    )

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache")
    pinned = reader.current()
    assert pinned.manifest.publication_id == manifest.publication_id

    conn = pinned.open_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        assert row[0] >= 1
    finally:
        conn.close()


def test_current_is_cached_within_the_ttl_and_does_not_re_download(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    publisher.publish(sample_db, created_by="run-1", ingest_run_id="run-1", summary={})

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache", cache_ttl_seconds=60)
    first = reader.current()
    second = reader.current()
    assert first.local_path == second.local_path


def test_new_publication_replaces_the_pinned_snapshot_after_ttl_elapses(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    gen1 = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache", cache_ttl_seconds=0)
    first = reader.current()
    assert first.manifest.publication_id == gen1.publication_id
    first_path = first.local_path

    gen2 = publisher.publish(
        sample_db,
        created_by="run-2",
        ingest_run_id="run-2",
        summary={"gen": 2},
        parent_publication_id=gen1.publication_id,
    )
    second = reader.current()
    assert second.manifest.publication_id == gen2.publication_id
    # The previous generation's local file is cleaned up once replaced.
    assert not first_path.exists()


def test_failed_refresh_keeps_serving_the_previous_verified_snapshot(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    gen1 = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )

    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache", cache_ttl_seconds=0)
    first = reader.current()
    assert first.manifest.publication_id == gen1.publication_id

    # Corrupt the *active manifest* itself in place (simulating a broken
    # publish reachable only by direct tampering, since the writer's own
    # compare-and-swap can't produce this) so the next refresh fails.
    fake_s3._objects[layout.ACTIVE_MANIFEST_KEY] = _FakeObject(
        etag="broken-etag", version_id="broken-v", content=b"{not valid json"
    )

    still_pinned = reader.current()
    assert still_pinned.manifest.publication_id == gen1.publication_id
    assert still_pinned.local_path == first.local_path
    assert reader.last_refresh_error is not None


def test_corrupt_snapshot_download_is_rejected_and_prior_snapshot_kept(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = SnapshotPublisher(fake_s3, work_dir=tmp_path / "work", owner_id="w1")
    gen1 = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )
    reader = SnapshotReader(fake_s3, cache_dir=tmp_path / "cache", cache_ttl_seconds=0)
    first = reader.current()

    gen2 = publisher.publish(
        sample_db,
        created_by="run-2",
        ingest_run_id="run-2",
        summary={"gen": 2},
        parent_publication_id=gen1.publication_id,
    )
    # Tamper with the *uploaded snapshot bytes* for generation 2, after
    # publish already computed and stored its correct sha256 in the
    # manifest -- simulating bit rot / a corrupted transfer.
    existing = fake_s3._objects[gen2.snapshot_key]
    fake_s3._objects[gen2.snapshot_key] = _FakeObject(
        etag=existing.etag, version_id=existing.version_id, content=b"corrupted bytes"
    )

    # design.md 7.2: a failed refresh does not evict the previous valid
    # snapshot and does not raise while one is available -- read-only
    # dashboard functions keep working (Requirement 20.6). current() keeps
    # returning generation 1; the failure is recorded, not propagated.
    still_current = reader.current()
    assert still_current.manifest.publication_id == gen1.publication_id
    assert isinstance(reader.last_refresh_error, SnapshotVerificationError)

    # Generation 1's local file was never evicted.
    assert first.local_path.exists()
    still_pinned_conn = first.open_connection()
    still_pinned_conn.execute("SELECT 1").fetchone()
    still_pinned_conn.close()
