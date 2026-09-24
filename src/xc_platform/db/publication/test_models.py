from __future__ import annotations

import pytest

from xc_platform.db.migrator import latest_schema_version
from xc_platform.db.publication.errors import InvalidManifestError
from xc_platform.db.publication.models import Manifest, WriterLease


def _valid_manifest_dict(**overrides: object) -> dict[str, object]:
    publication_id = "11111111-1111-4111-8111-111111111111"
    base: dict[str, object] = {
        "publication_id": publication_id,
        "parent_publication_id": None,
        "snapshot_key": f"database/snapshots/{publication_id}/xc.db",
        "snapshot_version_id": "v1",
        "sha256": "a" * 64,
        "byte_size": 1024,
        "schema_version": 1,
        "created_at": "2026-09-22T00:00:00Z",
        "created_by": "ingest-run-1",
        "summary": {"results": 4203},
    }
    base.update(overrides)
    return base


def test_valid_manifest_round_trips_through_json() -> None:
    manifest = Manifest.from_dict(_valid_manifest_dict())
    reloaded = Manifest.from_json(manifest.to_json())
    assert reloaded == manifest


def test_rejects_non_uuid_publication_id() -> None:
    with pytest.raises(InvalidManifestError, match="publication_id"):
        Manifest.from_dict(_valid_manifest_dict(publication_id="not-a-uuid"))


def test_rejects_snapshot_key_path_traversal() -> None:
    with pytest.raises(InvalidManifestError, match="snapshot_key"):
        Manifest.from_dict(
            _valid_manifest_dict(snapshot_key="database/snapshots/../../etc/passwd")
        )


def test_rejects_snapshot_key_outside_the_layout() -> None:
    with pytest.raises(InvalidManifestError, match="snapshot_key"):
        Manifest.from_dict(_valid_manifest_dict(snapshot_key="raw/whatever.db"))


def test_snapshot_key_may_reference_a_different_publication_for_restore() -> None:
    # design.md 7.4: a restore manifest's snapshot_key legitimately points
    # at an older publication's unedited snapshot, not its own ID.
    other_id = "22222222-2222-4222-8222-222222222222"
    manifest = Manifest.from_dict(
        _valid_manifest_dict(snapshot_key=f"database/snapshots/{other_id}/xc.db")
    )
    assert manifest.snapshot_key == f"database/snapshots/{other_id}/xc.db"


def test_rejects_bad_sha256() -> None:
    with pytest.raises(InvalidManifestError, match="sha256"):
        Manifest.from_dict(_valid_manifest_dict(sha256="not-hex"))


def test_rejects_non_positive_byte_size() -> None:
    with pytest.raises(InvalidManifestError, match="byte_size"):
        Manifest.from_dict(_valid_manifest_dict(byte_size=0))


def test_rejects_schema_version_newer_than_supported() -> None:
    with pytest.raises(InvalidManifestError, match="schema_version"):
        Manifest.from_dict(
            _valid_manifest_dict(schema_version=latest_schema_version() + 1)
        )


def test_rejects_malformed_timestamp() -> None:
    with pytest.raises(InvalidManifestError, match="created_at"):
        Manifest.from_dict(_valid_manifest_dict(created_at="not-a-timestamp"))


def test_rejects_negative_summary_count() -> None:
    with pytest.raises(InvalidManifestError, match="summary"):
        Manifest.from_dict(_valid_manifest_dict(summary={"results": -1}))


def test_rejects_malformed_json() -> None:
    with pytest.raises(InvalidManifestError, match="JSON"):
        Manifest.from_json("{not json")


def test_rejects_json_that_is_not_an_object() -> None:
    with pytest.raises(InvalidManifestError, match="object"):
        Manifest.from_json("[1, 2, 3]")


def test_writer_lease_expiry() -> None:
    from datetime import UTC, datetime, timedelta

    lease = WriterLease.new(owner_id="worker-1", ingest_run_id="run-1", ttl_seconds=60)
    assert not lease.is_expired(now=datetime.now(UTC))
    assert lease.is_expired(now=datetime.now(UTC) + timedelta(seconds=61))


def test_writer_lease_round_trips_through_json() -> None:
    lease = WriterLease.new(owner_id="worker-1", ingest_run_id="run-1", ttl_seconds=60)
    reloaded = WriterLease.from_json(lease.to_json())
    assert reloaded == lease
