"""CLI entry point: run the one-time historical backfill end to end (Task 5).

Usage::

    python -m xc_platform.cli.backfill <db-path> [--report <path>]

Bootstraps/migrates ``<db-path>``, backfills the frozen baseline CSV, seeds
school/athlete aliases from the legacy curation scripts (Task 5.2), and
writes a reconciliation report comparing the result against
``tests/fixtures/baseline/`` (Task 5.4).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xc_platform.db.migrator import bootstrap
from xc_platform.migration.aliases import seed_name_corrections, seed_school_aliases
from xc_platform.migration.backfill import run_backfill
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter
from xc_platform.migration.parity import generate_reconciliation_report

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = REPO_ROOT / "data" / "merged" / "season_results.csv"
DEFAULT_CORRECTIONS = REPO_ROOT / "name_corrections.csv"
DEFAULT_BASELINE_DIR = REPO_ROOT / "tests" / "fixtures" / "baseline"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path")
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--corrections", default=str(DEFAULT_CORRECTIONS))
    parser.add_argument("--baseline-dir", default=str(DEFAULT_BASELINE_DIR))
    parser.add_argument("--report", default=None)
    parser.add_argument("--skip-parity", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    manifest_path = Path(args.baseline_dir) / "manifest.json"
    expected_sha256 = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_sha256 = manifest["canonical_dataset"]["sha256"]

    conn = bootstrap(args.db_path)
    report = run_backfill(
        conn,
        args.csv,
        correlation_id="cli-backfill",
        expected_sha256=expected_sha256,
    )
    print(
        f"backfill: {report.inserted_results} inserted, "
        f"{len(report.quarantined)} quarantined"
    )
    for q in report.quarantined:
        print(f"  quarantined row {q.row_index}: {q.reason}")

    writer = HistoricalCanonicalWriter(conn)
    alias_count = seed_school_aliases(writer, source_id=report.source_id)
    print(f"school aliases seeded: {alias_count}")
    if Path(args.corrections).is_file():
        summary = seed_name_corrections(
            writer, args.corrections, source_id=report.source_id
        )
        print(
            f"name corrections: {summary.applied_aliases} applied, "
            f"{summary.review_cases} review, {summary.keep_decisions} kept"
        )

    if not args.skip_parity:
        report_path = (
            args.report or f"{Path(args.db_path).with_suffix('')}.reconciliation.json"
        )
        parity_report = generate_reconciliation_report(
            conn, args.baseline_dir, report_path=report_path
        )
        print(
            f"parity: passed={parity_report.passed} "
            f"discrepancies={len(parity_report.discrepancies)} "
            f"observations={len(parity_report.observations)}"
        )
        print(f"reconciliation report written to {report_path}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
