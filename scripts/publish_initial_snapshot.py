"""One-off: publish an accepted database file as a snapshot generation
(Task 18.1's production cutover). Not part of the ingest workflow --
that path (``ingest/workflow.py``) publishes automatically after each
resolved intake run. This script exists only to seed the very first
generation from ``.release/xc-accepted.db`` (the Task 5 backfilled,
0-discrepancy baseline) before any real ingest run has happened.

Usage::

    python -m scripts.publish_initial_snapshot <db-path> <bucket> [--region us-east-1]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xc_platform.db.publication.s3_client import Boto3S3Client
from xc_platform.db.publication.writer import SnapshotPublisher


def _summary_from_reconciliation_report(report_path: Path) -> dict[str, int]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    totals = next(
        obs["actual"]
        for obs in report["observations"]
        if obs["check"] == "counts.quarantine_accounts_for_gap"
    )
    return {
        "inserted": totals["canonical_results"],
        "quarantined": totals["quarantined"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path")
    parser.add_argument("bucket")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--created-by", default="owner-cutover")
    parser.add_argument("--ingest-run-id", default="initial-generation")
    parser.add_argument("--reconciliation-report", default=None)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    report_arg = args.reconciliation_report
    report_path = Path(report_arg) if report_arg else None
    summary = _summary_from_reconciliation_report(report_path) if report_path else {}

    s3 = Boto3S3Client.create(bucket=args.bucket, region=args.region)
    publisher = SnapshotPublisher(
        s3,
        work_dir=Path("/tmp/publish-work"),  # noqa: S108 -- container-local scratch, never authoritative
        owner_id="cutover-script",
    )
    manifest = publisher.publish(
        Path(args.db_path),
        created_by=args.created_by,
        ingest_run_id=args.ingest_run_id,
        summary=summary,
        reconciliation_report_path=report_path,
    )
    print(f"published publication_id={manifest.publication_id}")
    print(f"  snapshot_key={manifest.snapshot_key}")
    print(f"  snapshot_version_id={manifest.snapshot_version_id}")
    print(f"  sha256={manifest.sha256}")
    print(f"  byte_size={manifest.byte_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
