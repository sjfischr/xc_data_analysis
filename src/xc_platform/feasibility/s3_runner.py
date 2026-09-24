"""Task 3.4 runner -- creates a disposable bucket, proves F6, then removes it.

Teardown runs in a ``finally`` block so an exception mid-scenario still deletes
every object version and the bucket. A leaked bucket costs money and violates
the probe's "disposable" contract.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from xc_platform.feasibility import s3_scenarios as scenarios
from xc_platform.feasibility.s3_protocol import (
    ProtocolProbe,
    S3Cli,
    build_representative_snapshot,
    new_bucket_name,
)


def run(
    *,
    aws_path: str,
    region: str,
    workdir: Path,
    source_csv: Path,
    keep_bucket: bool = False,
) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    cli = S3Cli(aws_path=aws_path, region=region)
    bucket = new_bucket_name()
    probe = ProtocolProbe(cli=cli, bucket=bucket, workdir=workdir)

    findings: dict[str, Any] = {"bucket": bucket, "region": region}
    started = time.monotonic()

    snapshot_path = workdir / "xc-snapshot.db"
    findings["snapshot"] = build_representative_snapshot(snapshot_path, source_csv)

    try:
        findings["bucket_setup"] = probe.create_bucket()
        findings["publish"] = scenarios.scenario_publish(
            probe, snapshot_path, findings["snapshot"]
        )
        findings["reader"] = scenarios.scenario_reader(probe, findings["snapshot"])
        findings["writer_lease_race"] = scenarios.scenario_writer_lease_race(probe)
        findings["lease_expiry"] = scenarios.scenario_lease_expiry(probe)
        findings["manifest_cas_race"] = scenarios.scenario_manifest_cas_race(
            probe, findings["publish"], snapshot_path, findings["snapshot"]
        )
        findings["failure_injection"] = scenarios.scenario_failure_injection(
            probe, snapshot_path, findings["snapshot"]
        )
        findings["corrupt_download"] = scenarios.scenario_corrupt_download(probe)
        findings["restore"] = scenarios.scenario_restore(probe, findings["publish"])
        findings["orphan_cleanup"] = _orphan_cleanup(probe, findings)
        findings["error"] = None
    except Exception as failure:
        findings["error"] = f"{type(failure).__name__}: {failure}"
    finally:
        if keep_bucket:
            findings["teardown"] = {"skipped": "keep_bucket requested"}
        else:
            findings["teardown"] = probe.delete_bucket()
        findings["total_elapsed_s"] = round(time.monotonic() - started, 3)
        findings["cli_calls"] = cli.calls

    findings["verdict"] = _verdict(findings)
    return findings


def _orphan_cleanup(probe: ProtocolProbe, findings: dict[str, Any]) -> dict[str, Any]:
    """Identify snapshots no manifest version references -- the cleanup input."""
    listing = (
        probe.cli.run(
            "s3api",
            "list-objects-v2",
            "--bucket",
            probe.bucket,
            "--prefix",
            "database/snapshots/",
            check=False,
            note="list all snapshots",
        ).json()
        or {}
    )
    all_keys = {item["Key"] for item in listing.get("Contents") or []}

    referenced = {findings["publish"]["snapshot_key"]}
    winner = (findings.get("manifest_cas_race") or {}).get("winner")
    if winner:
        referenced.add(winner["snapshot_key"])

    orphans = sorted(all_keys - referenced)
    expected = set(
        (findings.get("manifest_cas_race") or {}).get("loser_snapshots_are_orphans")
        or []
    )
    expected.add(
        (findings.get("failure_injection") or {})
        .get("crash_after_upload", {})
        .get("orphan_snapshot_key", "")
    )
    expected.discard("")

    return {
        "snapshots_total": len(all_keys),
        "referenced_by_a_manifest": sorted(referenced),
        "orphan_candidates": orphans,
        "orphans_match_expected": set(orphans) == expected,
        "note": (
            "Orphans are snapshots uploaded by a writer that never won the "
            "manifest swap. They are harmless (no reader can reach them) and "
            "are removed by an S3 lifecycle rule, never by deleting a "
            "referenced generation."
        ),
    }


def _verdict(findings: dict[str, Any]) -> dict[str, Any]:
    """Reduce the scenarios to explicit pass/fail claims for gate F6."""
    checks = {
        "bucket_is_versioned": (findings.get("bucket_setup") or {}).get("versioning")
        == "Enabled",
        "bucket_is_encrypted": bool(
            (findings.get("bucket_setup") or {}).get("encryption_algorithm")
        ),
        "public_access_blocked": (findings.get("bucket_setup") or {}).get(
            "public_access_blocked"
        )
        is True,
        "snapshot_keys_are_immutable": (findings.get("publish") or {}).get(
            "immutability_enforced"
        )
        is True,
        "reader_verifies_snapshot": (findings.get("reader") or {}).get("verified")
        is True,
        "single_writer_lease_enforced": (
            findings.get("writer_lease_race") or {}
        ).get("single_writer_enforced")
        is True,
        "stale_lease_takeover_rejected": (findings.get("lease_expiry") or {}).get(
            "takeover_with_stale_etag_rejected"
        )
        is True,
        "lost_update_prevented": (findings.get("manifest_cas_race") or {}).get(
            "lost_update_prevented"
        )
        is True,
        "failures_preserve_active_generation": (
            findings.get("failure_injection") or {}
        ).get("active_publication_unchanged")
        is True,
        "reader_survives_failed_publish": (
            findings.get("failure_injection") or {}
        ).get("reader_still_verifies")
        is True,
        "corruption_is_detected": (findings.get("corrupt_download") or {}).get(
            "detection_works"
        )
        is True,
        "restore_is_append_only": (findings.get("restore") or {}).get(
            "parent_is_prior_publication"
        )
        is True,
        "restored_snapshot_verifies": (findings.get("restore") or {}).get(
            "restored_snapshot_verifies"
        )
        is True,
        "bucket_torn_down": (findings.get("teardown") or {}).get("bucket_removed")
        is True,
    }
    return {
        "checks": checks,
        "passed": sum(1 for value in checks.values() if value),
        "total": len(checks),
        "f6_pass": all(checks.values()),
    }


def render_markdown(findings: dict[str, Any]) -> str:
    verdict = findings.get("verdict", {})
    setup = findings.get("bucket_setup", {})
    publish = findings.get("publish", {})
    reader = findings.get("reader", {})
    restore = findings.get("restore", {})
    snapshot = findings.get("snapshot", {})

    lines = [
        "# Feasibility probe 3.4 -- SQLite/S3 publication protocol (gate F6)",
        "",
        f"- Bucket: `{findings.get('bucket')}` in `{findings.get('region')}` "
        "(disposable; deleted at the end of the run)",
        f"- Total elapsed: {findings.get('total_elapsed_s')}s",
        f"- Verdict: **{'PASS' if verdict.get('f6_pass') else 'FAIL'}** "
        f"({verdict.get('passed')}/{verdict.get('total')} checks)",
        "",
        "## Gate checks",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for name, value in (verdict.get("checks") or {}).items():
        lines.append(f"| {name.replace('_', ' ')} | {'PASS' if value else 'FAIL'} |")

    lines += [
        "",
        "## Bucket configuration",
        "",
        f"- Versioning: `{setup.get('versioning')}`",
        f"- Default encryption: `{setup.get('encryption_algorithm')}`",
        f"- Public access fully blocked: `{setup.get('public_access_blocked')}`",
        "",
        "## Measured timings",
        "",
        "| Operation | Value |",
        "|---|---|",
        f"| Snapshot size | {publish.get('snapshot_mib')} MiB "
        f"({snapshot.get('rows')} rows) |",
        f"| Snapshot upload | {publish.get('snapshot_upload_s')}s |",
        f"| Manifest put | {publish.get('manifest_put_s')}s |",
        f"| Manifest fetch (reader) | {reader.get('manifest_fetch_s')}s |",
        f"| Snapshot download | {reader.get('snapshot_download_s')}s "
        f"({reader.get('download_mib_per_s')} MiB/s) |",
        f"| Verify (sha + integrity) | {reader.get('verify_elapsed_s')}s |",
        f"| Restore (manifest CAS) | {restore.get('restore_elapsed_s')}s |",
        "",
        "## Concurrency",
        "",
        f"- Lease race winners: "
        f"{(findings.get('writer_lease_race') or {}).get('winners')} of 2 "
        f"(error codes for losers: "
        f"{(findings.get('writer_lease_race') or {}).get('loser_error_codes')})",
        f"- Manifest CAS winners: "
        f"{(findings.get('manifest_cas_race') or {}).get('winners')} of 2",
        f"- Orphan snapshots left by the losing writer: "
        f"{(findings.get('orphan_cleanup') or {}).get('orphan_candidates')}",
        "",
        "## Failure injection",
        "",
        "| Injected failure | Active generation preserved |",
        "|---|---|",
    ]
    injection = findings.get("failure_injection") or {}
    for key in ("crash_before_upload", "crash_after_upload"):
        block = injection.get(key) or {}
        lines.append(
            f"| {block.get('step')} | "
            f"{not block.get('active_generation_affected')} |"
        )
    lines += [
        f"| manifest CAS with stale ETag | "
        f"{injection.get('stale_manifest_swap_rejected')} |",
        "",
        f"Active publication before: `{injection.get('active_before')}`; "
        f"after: `{injection.get('active_after')}`.",
        "",
        "## Corruption detection",
        "",
        f"- Clean snapshot verifies: "
        f"{(findings.get('corrupt_download') or {}).get('clean_verified')}",
        f"- Corrupted snapshot verifies: "
        f"{(findings.get('corrupt_download') or {}).get('corrupted_verified')}",
        f"- SQLite integrity_check on corrupted file: "
        f"`{(findings.get('corrupt_download') or {}).get('corrupted_integrity_check')}`",
        "",
        "## Teardown",
        "",
        f"- Object versions deleted: "
        f"{(findings.get('teardown') or {}).get('versions_deleted')}",
        f"- Bucket removed: {(findings.get('teardown') or {}).get('bucket_removed')}",
        "",
    ]
    if findings.get("error"):
        lines += ["## Error", "", f"`{findings['error']}`", ""]
    return "\n".join(lines)
