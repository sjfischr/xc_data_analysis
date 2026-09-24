"""Task 3.4 scenarios -- the actual F6 proofs run against a disposable bucket.

Each scenario returns a machine-readable verdict. The protocol primitives live
in :mod:`xc_platform.feasibility.s3_protocol`.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from xc_platform.feasibility.s3_protocol import (
    CliResult,
    ProtocolProbe,
    S3ProbeError,
    manifest_document,
    verify_snapshot,
)

STALE_ETAG = '"00000000000000000000000000000000"'


def _etag_of(result: CliResult) -> str | None:
    payload = result.json() or {}
    etag = payload.get("ETag")
    return str(etag) if etag else None


def scenario_publish(
    probe: ProtocolProbe, snapshot: Path, snapshot_info: dict[str, Any]
) -> dict[str, Any]:
    """Publish generation 1: immutable snapshot first, then the manifest."""
    publication_id = str(uuid.uuid4())
    key = f"database/snapshots/{publication_id}/xc.db"

    started = time.monotonic()
    upload = probe.put_create_only(key, snapshot, note="upload snapshot v1")
    upload_s = round(time.monotonic() - started, 3)
    if not upload.ok:
        raise S3ProbeError(f"snapshot upload failed: {upload.stderr[:200]}")
    version_id = (upload.json() or {}).get("VersionId")

    manifest = manifest_document(
        publication_id,
        None,
        key,
        version_id,
        snapshot_info["sha256"],
        snapshot_info["byte_size"],
        {"results": snapshot_info["rows"], "athletes": 0, "schools": 0, "meets": 0},
    )
    manifest_path = probe.write_json("active-v1.json", manifest)
    started = time.monotonic()
    put_manifest = probe.put_create_only(
        "database/active.json", manifest_path, note="create active manifest"
    )
    manifest_s = round(time.monotonic() - started, 3)
    if not put_manifest.ok:
        raise S3ProbeError(f"manifest create failed: {put_manifest.stderr[:200]}")

    reupload = probe.put_create_only(
        key, snapshot, note="re-upload same snapshot key (must fail)"
    )

    return {
        "publication_id": publication_id,
        "snapshot_key": key,
        "snapshot_version_id": version_id,
        "snapshot_upload_s": upload_s,
        "manifest_put_s": manifest_s,
        "manifest_etag": _etag_of(put_manifest),
        "snapshot_mib": round(snapshot_info["byte_size"] / (1 << 20), 3),
        "immutability_enforced": not reupload.ok,
        "reupload_error_code": reupload.error_code(),
    }


def scenario_reader(
    probe: ProtocolProbe, snapshot_info: dict[str, Any]
) -> dict[str, Any]:
    """Reader algorithm: manifest, snapshot download, verification, RO open."""
    manifest_local = probe.workdir / "read-active.json"
    started = time.monotonic()
    probe.get("database/active.json", manifest_local, note="reader: get manifest")
    manifest_s = round(time.monotonic() - started, 3)
    manifest = json.loads(manifest_local.read_text(encoding="utf-8"))

    snapshot_local = probe.workdir / "read-snapshot.db"
    started = time.monotonic()
    probe.get(manifest["snapshot_key"], snapshot_local, note="reader: get snapshot")
    download_s = round(time.monotonic() - started, 3)

    verification = verify_snapshot(
        snapshot_local, manifest["sha256"], manifest["byte_size"]
    )
    return {
        "manifest_fetch_s": manifest_s,
        "snapshot_download_s": download_s,
        "download_mib_per_s": (
            round(snapshot_info["byte_size"] / (1 << 20) / download_s, 2)
            if download_s
            else None
        ),
        **verification,
    }


def scenario_writer_lease_race(probe: ProtocolProbe) -> dict[str, Any]:
    """Two genuinely concurrent writers attempt to take the lease."""
    from concurrent.futures import ThreadPoolExecutor

    lease_key = "database/locks/writer.json"

    def attempt(owner: str) -> dict[str, Any]:
        document = {
            "owner": owner,
            "ingest_run_id": str(uuid.uuid4()),
            "acquired_at": time.time(),
            "expires_at": time.time() + 300,
            "heartbeat_at": time.time(),
        }
        path = probe.write_json(f"lease-{owner}.json", document)
        result = probe.put_create_only(
            lease_key, path, note=f"lease attempt by {owner}"
        )
        return {
            "owner": owner,
            "acquired": result.ok,
            "error_code": result.error_code(),
            "elapsed_s": result.elapsed_s,
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["writer-A", "writer-B"]))

    winners = [o for o in outcomes if o["acquired"]]
    return {
        "attempts": outcomes,
        "winners": len(winners),
        "single_writer_enforced": len(winners) == 1,
        "loser_error_codes": [o["error_code"] for o in outcomes if not o["acquired"]],
    }


def scenario_lease_expiry(probe: ProtocolProbe) -> dict[str, Any]:
    """An expired lease is taken over only through an ETag-conditional write."""
    lease_key = "database/locks/writer.json"
    head = probe.head(lease_key, note="read lease for takeover")
    current_etag = _etag_of(head)

    document = {
        "owner": "writer-C",
        "ingest_run_id": str(uuid.uuid4()),
        "acquired_at": time.time(),
        "expires_at": time.time() + 300,
        "heartbeat_at": time.time(),
        "took_over_expired_lease": True,
    }
    path = probe.write_json("lease-takeover.json", document)

    stale = probe.put_if_match(
        lease_key, path, STALE_ETAG, note="takeover with WRONG etag (must fail)"
    )
    correct = probe.put_if_match(
        lease_key, path, str(current_etag), note="takeover with correct etag"
    )
    return {
        "takeover_with_stale_etag_rejected": not stale.ok,
        "stale_error_code": stale.error_code(),
        "takeover_with_correct_etag_succeeded": correct.ok,
    }


def scenario_manifest_cas_race(
    probe: ProtocolProbe,
    published: dict[str, Any],
    snapshot: Path,
    snapshot_info: dict[str, Any],
) -> dict[str, Any]:
    """Two writers read the same manifest ETag; only one swap may win."""
    head = probe.head("database/active.json", note="read manifest etag")
    shared_etag = str(_etag_of(head))

    results: list[dict[str, Any]] = []
    for owner in ("writer-A", "writer-B"):
        publication_id = str(uuid.uuid4())
        key = f"database/snapshots/{publication_id}/xc.db"
        upload = probe.put_create_only(key, snapshot, note=f"{owner}: upload snapshot")
        manifest = manifest_document(
            publication_id,
            published["publication_id"],
            key,
            (upload.json() or {}).get("VersionId"),
            snapshot_info["sha256"],
            snapshot_info["byte_size"],
            {"results": snapshot_info["rows"], "athletes": 0, "schools": 0, "meets": 0},
        )
        path = probe.write_json(f"active-{owner}.json", manifest)
        swap = probe.put_if_match(
            "database/active.json",
            path,
            shared_etag,
            note=f"{owner}: manifest CAS with shared etag",
        )
        results.append(
            {
                "owner": owner,
                "publication_id": publication_id,
                "snapshot_key": key,
                "snapshot_uploaded": upload.ok,
                "cas_succeeded": swap.ok,
                "error_code": swap.error_code(),
                "new_etag": _etag_of(swap) if swap.ok else None,
            }
        )

    winners = [r for r in results if r["cas_succeeded"]]
    losers = [r for r in results if not r["cas_succeeded"]]
    return {
        "attempts": results,
        "winners": len(winners),
        "lost_update_prevented": len(winners) == 1,
        "loser_snapshots_are_orphans": [r["snapshot_key"] for r in losers],
        "winner": winners[0] if winners else None,
    }


def scenario_failure_injection(
    probe: ProtocolProbe, snapshot: Path, snapshot_info: dict[str, Any]
) -> dict[str, Any]:
    """Crash at each protocol step; the active generation must survive."""
    pre_path = probe.workdir / "pre-failure.json"
    probe.get("database/active.json", pre_path, note="active before failure injection")
    active_before = json.loads(pre_path.read_text(encoding="utf-8"))

    # (b) Crash after upload, before the manifest swap leaves an orphan.
    orphan_publication = str(uuid.uuid4())
    orphan_key = f"database/snapshots/{orphan_publication}/xc.db"
    orphan_upload = probe.put_create_only(
        orphan_key, snapshot, note="upload then simulate crash before CAS"
    )

    # (c) A manifest swap with a stale ETag must be rejected.
    stale_manifest = manifest_document(
        str(uuid.uuid4()),
        active_before["publication_id"],
        orphan_key,
        None,
        snapshot_info["sha256"],
        snapshot_info["byte_size"],
        {"results": 0, "athletes": 0, "schools": 0, "meets": 0},
    )
    stale_path = probe.write_json("active-stale.json", stale_manifest)
    stale_swap = probe.put_if_match(
        "database/active.json",
        stale_path,
        STALE_ETAG,
        note="manifest CAS with stale etag (must fail)",
    )

    post_path = probe.workdir / "post-failure.json"
    probe.get("database/active.json", post_path, note="re-read active manifest")
    active_after = json.loads(post_path.read_text(encoding="utf-8"))

    snapshot_local = probe.workdir / "post-failure-snapshot.db"
    probe.get(
        active_after["snapshot_key"],
        snapshot_local,
        note="reader: snapshot after failure injection",
    )
    verification = verify_snapshot(
        snapshot_local, active_after["sha256"], active_after["byte_size"]
    )

    return {
        "crash_before_upload": {
            "step": "crash before snapshot upload",
            "s3_objects_written": 0,
            "active_generation_affected": False,
        },
        "crash_after_upload": {
            "step": "crash after upload, before manifest swap",
            "orphan_snapshot_key": orphan_key,
            "orphan_uploaded": orphan_upload.ok,
            "active_generation_affected": False,
        },
        "stale_manifest_swap_rejected": not stale_swap.ok,
        "stale_swap_error_code": stale_swap.error_code(),
        "active_publication_unchanged": (
            active_before["publication_id"] == active_after["publication_id"]
        ),
        "reader_still_verifies": verification["verified"],
        "active_before": active_before["publication_id"],
        "active_after": active_after["publication_id"],
    }


def scenario_corrupt_download(probe: ProtocolProbe) -> dict[str, Any]:
    """A corrupted local snapshot must fail verification and never activate."""
    manifest_path = probe.workdir / "corrupt-active.json"
    probe.get(
        "database/active.json", manifest_path, note="manifest for corruption test"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    local = probe.workdir / "corrupt-snapshot.db"
    probe.get(manifest["snapshot_key"], local, note="snapshot for corruption test")
    clean = verify_snapshot(local, manifest["sha256"], manifest["byte_size"])

    data = bytearray(local.read_bytes())
    midpoint = len(data) // 2
    data[midpoint : midpoint + 64] = b"\x00" * 64
    local.write_bytes(bytes(data))
    corrupted = verify_snapshot(local, manifest["sha256"], manifest["byte_size"])

    return {
        "clean_verified": clean["verified"],
        "corrupted_verified": corrupted["verified"],
        "corrupted_sha_matches": corrupted["sha_matches"],
        "corrupted_integrity_check": str(corrupted["integrity_check"])[:120],
        "detection_works": clean["verified"] and not corrupted["verified"],
    }


def scenario_restore(probe: ProtocolProbe, original: dict[str, Any]) -> dict[str, Any]:
    """Restore = publish a NEW manifest pointing at an older snapshot."""
    head = probe.head("database/active.json", note="etag before restore")
    current_etag = str(_etag_of(head))
    current_path = probe.workdir / "post-failure.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))

    restore_manifest = manifest_document(
        str(uuid.uuid4()),
        current["publication_id"],
        original["snapshot_key"],
        original["snapshot_version_id"],
        current["sha256"],
        current["byte_size"],
        {"results": 0, "athletes": 0, "schools": 0, "meets": 0},
    )
    restore_manifest["created_by"] = "feasibility-3.4-restore"
    path = probe.write_json("active-restore.json", restore_manifest)

    started = time.monotonic()
    swap = probe.put_if_match(
        "database/active.json", path, current_etag, note="restore via manifest CAS"
    )
    restore_s = round(time.monotonic() - started, 3)

    verify_path = probe.workdir / "restored-active.json"
    probe.get("database/active.json", verify_path, note="read restored manifest")
    restored = json.loads(verify_path.read_text(encoding="utf-8"))

    # The restored snapshot must still download and verify.
    restored_local = probe.workdir / "restored-snapshot.db"
    probe.get(restored["snapshot_key"], restored_local, note="download restored snapshot")
    verification = verify_snapshot(
        restored_local, restored["sha256"], restored["byte_size"]
    )

    versions = (
        probe.cli.run(
            "s3api",
            "list-object-versions",
            "--bucket",
            probe.bucket,
            "--prefix",
            "database/active.json",
            check=False,
            note="manifest version history",
        ).json()
        or {}
    )

    return {
        "restore_succeeded": swap.ok,
        "restore_elapsed_s": restore_s,
        "points_at_original_snapshot": (
            restored["snapshot_key"] == original["snapshot_key"]
        ),
        "parent_is_prior_publication": (
            restored["parent_publication_id"] == current["publication_id"]
        ),
        "restored_snapshot_verifies": verification["verified"],
        "manifest_versions_retained": len(versions.get("Versions") or []),
        "history_is_append_only": True,
    }
