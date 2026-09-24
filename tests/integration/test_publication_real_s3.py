"""Real-S3 confirmation of the production publication client (Task 6.5).

Runs the actual :class:`SnapshotPublisher`/:class:`SnapshotReader` classes
against :class:`Boto3S3Client` on a disposable, versioned, encrypted,
public-access-blocked bucket created and destroyed by this test -- the same
bucket configuration Task 3.4's feasibility probe proved works
(:mod:`xc_platform.feasibility.s3_protocol`), now driving the production
code path instead of the AWS CLI.

This is marked ``live`` (opt-in, never runs in ordinary CI -- see
pyproject.toml's marker definitions and the CI workflow's
``-m "not live and not integration"`` filter) because it needs real AWS
credentials and creates real (if disposable) cloud resources. Run
explicitly with::

    pytest tests/integration/test_publication_real_s3.py -m live -v

On a machine with Norton (or similar) TLS interception, set
``AWS_CA_BUNDLE`` to the interception root first, exactly as documented in
``xc_platform.feasibility.s3_protocol``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from xc_platform.db.migrator import bootstrap
from xc_platform.db.publication.errors import WriterBusyError
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import Boto3S3Client
from xc_platform.db.publication.writer import SnapshotPublisher

pytestmark = pytest.mark.live

REGION = "us-east-1"


def _require_boto3() -> None:
    pytest.importorskip("boto3")


@pytest.fixture
def bucket_name() -> Iterator[str]:
    _require_boto3()
    import boto3

    name = f"xc-publication-live-test-{uuid.uuid4().hex[:12]}"
    s3api = boto3.client("s3", region_name=REGION)
    s3api.create_bucket(Bucket=name)
    s3api.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3api.put_bucket_versioning(
        Bucket=name, VersioningConfiguration={"Status": "Enabled"}
    )
    s3api.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    s3api.put_bucket_ownership_controls(
        Bucket=name,
        OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
    )
    try:
        yield name
    finally:
        paginator = s3api.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=name):
            for group in ("Versions", "DeleteMarkers"):
                for item in page.get(group, []):
                    s3api.delete_object(
                        Bucket=name, Key=item["Key"], VersionId=item["VersionId"]
                    )
        s3api.delete_bucket(Bucket=name)


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "sample.db"
    conn = bootstrap(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return db_path


def test_publish_read_republish_and_restore_against_real_s3(
    bucket_name: str, sample_db: Path, tmp_path: Path
) -> None:
    s3 = Boto3S3Client.create(bucket_name, region=REGION)
    publisher = SnapshotPublisher(s3, work_dir=tmp_path / "work", owner_id="live-test")
    reader = SnapshotReader(s3, cache_dir=tmp_path / "cache", cache_ttl_seconds=0)

    gen1 = publisher.publish(
        sample_db,
        created_by="live-test-run-1",
        ingest_run_id="run-1",
        summary={"gen": 1},
    )
    pinned = reader.current()
    assert pinned.manifest.publication_id == gen1.publication_id
    conn = pinned.open_connection()
    conn.execute("SELECT 1").fetchone()
    conn.close()

    gen2 = publisher.publish(
        sample_db,
        created_by="live-test-run-2",
        ingest_run_id="run-2",
        summary={"gen": 2},
        parent_publication_id=gen1.publication_id,
    )
    pinned2 = reader.current()
    assert pinned2.manifest.publication_id == gen2.publication_id

    restored = publisher.restore(
        target_snapshot_key=gen1.snapshot_key,
        target_sha256=gen1.sha256,
        target_byte_size=gen1.byte_size,
        target_schema_version=gen1.schema_version,
        current_active_publication_id=gen2.publication_id,
        created_by="live-test-restore",
        ingest_run_id="restore-1",
        summary=gen1.summary,
    )
    assert restored.snapshot_key == gen1.snapshot_key
    pinned3 = reader.current()
    assert pinned3.manifest.publication_id == restored.publication_id


def test_concurrent_writers_against_real_s3(
    bucket_name: str, sample_db: Path, tmp_path: Path
) -> None:
    s3 = Boto3S3Client.create(bucket_name, region=REGION)
    holder = SnapshotPublisher(
        s3, work_dir=tmp_path / "holder", owner_id="holder", lease_ttl_seconds=60
    )
    lease_etag = holder._acquire_lease(ingest_run_id="run-A")
    try:
        contender = SnapshotPublisher(
            s3, work_dir=tmp_path / "contender", owner_id="contender"
        )
        with pytest.raises(WriterBusyError):
            contender.publish(
                sample_db,
                created_by="run-B",
                ingest_run_id="run-B",
                summary={},
                parent_publication_id=None,
            )
    finally:
        holder._release_lease(lease_etag)


def test_stale_lease_takeover_against_real_s3(
    bucket_name: str, sample_db: Path, tmp_path: Path
) -> None:
    s3 = Boto3S3Client.create(bucket_name, region=REGION)
    stale = SnapshotPublisher(
        s3, work_dir=tmp_path / "stale", owner_id="stale", lease_ttl_seconds=1
    )
    stale._acquire_lease(ingest_run_id="run-A")

    time.sleep(2)

    successor = SnapshotPublisher(
        s3, work_dir=tmp_path / "successor", owner_id="successor"
    )
    manifest = successor.publish(
        sample_db,
        created_by="run-B",
        ingest_run_id="run-B",
        summary={},
        parent_publication_id=None,
    )
    assert manifest.created_by == "run-B"
