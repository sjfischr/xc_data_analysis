"""Writer publish/restore tests (Task 6.3-6.5): success paths, writer races,
stale leases, interrupted uploads, manifest conflicts, and restore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xc_platform.db.publication import layout
from xc_platform.db.publication.errors import (
    PublicationConflictError,
    SnapshotVerificationError,
    WriterBusyError,
)
from xc_platform.db.publication.models import Manifest
from xc_platform.db.publication.s3_client import FakeS3Client, _FakeObject
from xc_platform.db.publication.writer import SnapshotPublisher


def _publisher(
    s3: FakeS3Client, tmp_path: Path, *, owner_id: str = "worker-1"
) -> SnapshotPublisher:
    return SnapshotPublisher(
        s3, work_dir=tmp_path / f"work-{owner_id}", owner_id=owner_id
    )


# --- first (bootstrap) publish --------------------------------------------


def test_first_publish_bootstraps_active_json(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = _publisher(fake_s3, tmp_path)
    manifest = publisher.publish(
        sample_db,
        created_by="ingest-run-1",
        ingest_run_id="ingest-run-1",
        summary={"results": 0},
    )
    assert manifest.parent_publication_id is None
    assert fake_s3.head_object(layout.ACTIVE_MANIFEST_KEY) is not None
    assert fake_s3.head_object(manifest.snapshot_key) is not None
    # The lease is released after a successful publish.
    assert fake_s3.head_object(layout.WRITER_LEASE_KEY) is None


def test_publish_uploads_an_optional_reconciliation_report(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    report_path = tmp_path / "reconciliation.json"
    report_path.write_text('{"passed": true, "discrepancies": []}', encoding="utf-8")

    publisher = _publisher(fake_s3, tmp_path)
    manifest = publisher.publish(
        sample_db,
        created_by="run-1",
        ingest_run_id="run-1",
        summary={},
        reconciliation_report_path=report_path,
    )

    report_key = layout.reconciliation_report_key(manifest.publication_id)
    stored = fake_s3.head_object(report_key)
    assert stored is not None
    dest = tmp_path / "downloaded-report.json"
    fake_s3.get_object(report_key, dest)
    assert dest.read_text(encoding="utf-8") == report_path.read_text(encoding="utf-8")


def test_second_publish_supersedes_with_parent_lineage(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = _publisher(fake_s3, tmp_path)
    first = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"results": 0}
    )
    second = publisher.publish(
        sample_db,
        created_by="run-2",
        ingest_run_id="run-2",
        summary={"results": 1},
        parent_publication_id=first.publication_id,
    )
    assert second.parent_publication_id == first.publication_id

    active_tmp = tmp_path / "active-check.json"
    fake_s3.get_object(layout.ACTIVE_MANIFEST_KEY, active_tmp)
    active = Manifest.from_json(active_tmp.read_text())
    assert active.publication_id == second.publication_id


def test_publish_without_a_parent_is_refused_once_active_json_already_exists(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    """A caller can't accidentally publish a false "first generation" once
    real lineage exists -- catches a bug where only the opposite mismatch
    (bootstrap expected, active.json missing) was checked."""
    publisher = _publisher(fake_s3, tmp_path)
    first = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )

    with pytest.raises(PublicationConflictError) as excinfo:
        publisher.publish(
            sample_db,
            created_by="run-2",
            ingest_run_id="run-2",
            summary={"gen": 2},
            parent_publication_id=None,
        )
    assert excinfo.value.orphaned_publication_id is not None

    active_tmp = tmp_path / "active-check.json"
    fake_s3.get_object(layout.ACTIVE_MANIFEST_KEY, active_tmp)
    active = Manifest.from_json(active_tmp.read_text())
    assert active.publication_id == first.publication_id


# --- pre-publish verification ----------------------------------------------


def test_corrupt_local_database_is_refused_before_upload(
    fake_s3: FakeS3Client, tmp_path: Path
) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database")
    publisher = _publisher(fake_s3, tmp_path)
    with pytest.raises(SnapshotVerificationError):
        publisher.publish(
            corrupt, created_by="run-1", ingest_run_id="run-1", summary={}
        )
    # Nothing was uploaded, and the lease was released, not left dangling.
    assert fake_s3.object_keys() == []


# --- writer races and lease behavior ---------------------------------------


def test_concurrent_writer_is_rejected_while_lease_is_held(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    holder = _publisher(fake_s3, tmp_path, owner_id="holder")
    lease_etag = holder._acquire_lease(ingest_run_id="run-A")
    try:
        contender = _publisher(fake_s3, tmp_path, owner_id="contender")
        with pytest.raises(WriterBusyError, match="holder"):
            contender.publish(
                sample_db,
                created_by="run-B",
                ingest_run_id="run-B",
                summary={},
            )
    finally:
        holder._release_lease(lease_etag)


def test_expired_lease_is_taken_over(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    stale_holder = SnapshotPublisher(
        fake_s3,
        work_dir=tmp_path / "stale",
        owner_id="stale-worker",
        lease_ttl_seconds=0.01,
    )
    stale_holder._acquire_lease(ingest_run_id="run-A")

    import time

    time.sleep(0.05)

    successor = _publisher(fake_s3, tmp_path, owner_id="successor")
    manifest = successor.publish(
        sample_db, created_by="run-B", ingest_run_id="run-B", summary={}
    )
    assert manifest.created_by == "run-B"
    assert fake_s3.head_object(layout.WRITER_LEASE_KEY) is None


def test_release_never_deletes_a_lease_taken_over_by_another_writer(
    fake_s3: FakeS3Client, tmp_path: Path
) -> None:
    original = SnapshotPublisher(
        fake_s3, work_dir=tmp_path / "orig", owner_id="orig", lease_ttl_seconds=0.01
    )
    original_etag = original._acquire_lease(ingest_run_id="run-A")

    import time

    time.sleep(0.05)

    successor = _publisher(fake_s3, tmp_path, owner_id="successor")
    successor_etag = successor._acquire_lease(ingest_run_id="run-B")
    assert successor_etag != original_etag

    # The original holder's release must be a no-op now, not delete the
    # successor's lease out from under it.
    original._release_lease(original_etag)
    assert fake_s3.head_object(layout.WRITER_LEASE_KEY) is not None

    successor._release_lease(successor_etag)
    assert fake_s3.head_object(layout.WRITER_LEASE_KEY) is None


# --- interrupted upload / manifest conflict --------------------------------


def test_crash_after_snapshot_upload_before_manifest_cas_leaves_prior_active_intact(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = _publisher(fake_s3, tmp_path)
    first = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )

    def crash_before_manifest_write(key: str) -> None:
        if key == layout.ACTIVE_MANIFEST_KEY:
            raise RuntimeError("simulated process crash before manifest CAS")

    fake_s3.before_put = crash_before_manifest_write
    with pytest.raises(RuntimeError, match="simulated process crash"):
        publisher.publish(
            sample_db,
            created_by="run-2",
            ingest_run_id="run-2",
            summary={"gen": 2},
            parent_publication_id=first.publication_id,
        )
    fake_s3.before_put = None

    # The prior generation is still exactly what active.json points at.
    active_tmp = tmp_path / "active-check.json"
    fake_s3.get_object(layout.ACTIVE_MANIFEST_KEY, active_tmp)
    active = Manifest.from_json(active_tmp.read_text())
    assert active.publication_id == first.publication_id
    # The lease from the crashed attempt was still released (finally block).
    assert fake_s3.head_object(layout.WRITER_LEASE_KEY) is None


def test_manifest_compare_and_swap_conflict_is_reported_with_orphan_details(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = _publisher(fake_s3, tmp_path)
    first = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )

    injected = {"done": False}

    def inject_concurrent_writer(key: str) -> None:
        # Simulate another writer publishing generation 2 the instant before
        # this writer's own compare-and-swap executes.
        if key == layout.ACTIVE_MANIFEST_KEY and not injected["done"]:
            injected["done"] = True
            rogue = Manifest(
                publication_id="99999999-9999-4999-8999-999999999999",
                parent_publication_id=first.publication_id,
                snapshot_key=layout.snapshot_key(
                    "99999999-9999-4999-8999-999999999999"
                ),
                snapshot_version_id="rogue-v1",
                sha256="b" * 64,
                byte_size=1,
                schema_version=1,
                created_at="2026-09-22T00:00:01Z",
                created_by="rogue-writer",
                summary={},
            )
            fake_s3._objects[layout.ACTIVE_MANIFEST_KEY] = _FakeObject(
                etag="rogue-etag",
                version_id="rogue-v",
                content=rogue.to_json().encode(),
            )

    fake_s3.before_put = inject_concurrent_writer
    try:
        with pytest.raises(PublicationConflictError) as excinfo:
            publisher.publish(
                sample_db,
                created_by="run-2",
                ingest_run_id="run-2",
                summary={"gen": 2},
                parent_publication_id=first.publication_id,
            )
    finally:
        fake_s3.before_put = None

    assert excinfo.value.orphaned_publication_id is not None
    assert excinfo.value.orphaned_snapshot_key is not None
    # The orphaned snapshot was in fact uploaded (harmless, unreferenced).
    assert fake_s3.head_object(excinfo.value.orphaned_snapshot_key) is not None
    # But active.json still points at the rogue writer's generation, not ours.
    active_tmp = tmp_path / "active-check.json"
    fake_s3.get_object(layout.ACTIVE_MANIFEST_KEY, active_tmp)
    active = Manifest.from_json(active_tmp.read_text())
    assert active.publication_id == "99999999-9999-4999-8999-999999999999"
    assert active.publication_id != excinfo.value.orphaned_publication_id


# --- restore (Task 6.4) -----------------------------------------------------


def test_restore_republishes_an_older_snapshot_without_reuploading_it(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = _publisher(fake_s3, tmp_path)
    gen1 = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={"gen": 1}
    )
    gen2 = publisher.publish(
        sample_db,
        created_by="run-2",
        ingest_run_id="run-2",
        summary={"gen": 2},
        parent_publication_id=gen1.publication_id,
    )
    keys_before_restore = set(fake_s3.object_keys())

    restored = publisher.restore(
        target_snapshot_key=gen1.snapshot_key,
        target_sha256=gen1.sha256,
        target_byte_size=gen1.byte_size,
        target_schema_version=gen1.schema_version,
        current_active_publication_id=gen2.publication_id,
        created_by="admin@example.com",
        ingest_run_id="restore-1",
        summary=gen1.summary,
    )

    assert restored.snapshot_key == gen1.snapshot_key  # same object, not a copy
    assert restored.publication_id not in (gen1.publication_id, gen2.publication_id)
    assert restored.parent_publication_id == gen2.publication_id
    # No new snapshot object was created by restore -- only the manifest.
    assert set(fake_s3.object_keys()) - keys_before_restore == set()


def test_restore_refuses_a_target_that_fails_verification(
    fake_s3: FakeS3Client, sample_db: Path, tmp_path: Path
) -> None:
    publisher = _publisher(fake_s3, tmp_path)
    gen1 = publisher.publish(
        sample_db, created_by="run-1", ingest_run_id="run-1", summary={}
    )
    with pytest.raises(SnapshotVerificationError):
        publisher.restore(
            target_snapshot_key=gen1.snapshot_key,
            target_sha256="0" * 64,  # wrong hash
            target_byte_size=gen1.byte_size,
            target_schema_version=gen1.schema_version,
            current_active_publication_id=gen1.publication_id,
            created_by="admin@example.com",
            ingest_run_id="restore-1",
            summary={},
        )
