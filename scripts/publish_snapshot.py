"""Publish a prepared database as the next generation on top of the live
one (2026-09-24). Used when data was reviewed and committed in a local
environment and should go live without redoing the review.

Dry run by default: downloads and verifies the active snapshot, compares it
with the local database, and prints what would change. It refuses if any
result that is live today would disappear (unless --allow-removals).
``--yes`` publishes, with the active publication as the parent, so the
publisher's stale-base guard and compare-and-swap both apply.

After publishing, restart the API so its writer reloads the new generation;
until then the running writer holds the old data and cannot publish.

Usage::

    python scripts/publish_snapshot.py <db-path> <bucket> [--region us-east-1] [--yes]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xc_platform.db.migrator import latest_schema_version
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import Boto3S3Client
from xc_platform.db.publication.writer import SnapshotPublisher

SEASON_COUNTS = (
    "SELECT m.season_year, COUNT(*) FROM results r "
    "JOIN races ra ON ra.race_id = r.race_id "
    "JOIN meets m ON m.meet_id = ra.meet_id GROUP BY 1 ORDER BY 1"
)


def _prepare(db_path: Path, work: Path) -> Path:
    """A checkpointed, integrity-checked copy; the original is untouched."""
    copy = work / "candidate.db"
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(db_path) + suffix)
        if source.exists():
            shutil.copyfile(source, Path(str(copy) + suffix))
    conn = sqlite3.connect(copy)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("candidate database failed PRAGMA integrity_check")
    version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    if version != latest_schema_version():
        raise SystemExit(
            f"candidate schema version {version} != this code's "
            f"{latest_schema_version()}; migrate it first"
        )
    return copy


def _diff(live: sqlite3.Connection, candidate: Path) -> tuple[int, list[str]]:
    live.execute(f"ATTACH DATABASE '{candidate.as_posix()}' AS cand")
    report = []
    removed = live.execute(
        "SELECT COUNT(*) FROM main.results WHERE result_id NOT IN "
        "(SELECT result_id FROM cand.results)"
    ).fetchone()[0]
    added = live.execute(
        "SELECT COUNT(*) FROM cand.results WHERE result_id NOT IN "
        "(SELECT result_id FROM main.results)"
    ).fetchone()[0]
    report.append(f"results: +{added} added, -{removed} removed")
    live_seasons = dict(live.execute(SEASON_COUNTS).fetchall())
    cand_seasons = dict(
        live.execute(
            SEASON_COUNTS.replace("results", "cand.results")
            .replace("races ", "cand.races ")
            .replace("meets ", "cand.meets ")
        ).fetchall()
    )
    for season in sorted(set(live_seasons) | set(cand_seasons)):
        before, after = live_seasons.get(season, 0), cand_seasons.get(season, 0)
        mark = "" if before == after else "  <- changed"
        report.append(f"  {season}: {before} -> {after}{mark}")
    for kind, table, name_col in (
        ("schools", "schools", "display_name"),
        ("athletes", "athletes", "display_name"),
    ):
        new = live.execute(
            f"SELECT COUNT(*) FROM cand.{table} WHERE status = 'active' AND "  # noqa: S608
            f"{kind[:-1]}_id NOT IN (SELECT {kind[:-1]}_id FROM main.{table})"
        ).fetchone()[0]
        renamed = live.execute(
            f"SELECT m.{name_col}, c.{name_col} FROM main.{table} m "  # noqa: S608
            f"JOIN cand.{table} c USING ({kind[:-1]}_id) "
            f"WHERE m.{name_col} != c.{name_col}"
        ).fetchall()
        report.append(f"{kind}: +{new} new, {len(renamed)} renamed")
        report.extend(f"  renamed: {a!r} -> {b!r}" for a, b in renamed[:20])
    return removed, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("db_path")
    parser.add_argument("bucket")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--created-by", default="owner-local-review")
    parser.add_argument("--label", default="local-review-publish")
    parser.add_argument("--allow-removals", action="store_true")
    parser.add_argument("--yes", action="store_true", help="publish (default: dry run)")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="xc-publish-"))
    candidate = _prepare(Path(args.db_path), work)
    s3 = Boto3S3Client.create(bucket=args.bucket, region=args.region)
    active = SnapshotReader(s3, cache_dir=work / "active").current()
    print(
        f"active publication: {active.manifest.publication_id} "
        f"({active.manifest.created_at}, by {active.manifest.created_by})"
    )

    live = sqlite3.connect(active.local_path)
    removed, report = _diff(live, candidate)
    live.close()
    print("\n".join(report))

    if removed and not args.allow_removals:
        print(f"\nREFUSED: {removed} live result(s) would disappear.")
        return 2
    if not args.yes:
        print("\nDry run only. Re-run with --yes to publish.")
        return 0

    manifest = SnapshotPublisher(
        s3, work_dir=work / "pub", owner_id=args.created_by
    ).publish(
        candidate,
        created_by=args.created_by,
        ingest_run_id=args.label,
        summary={"inserted": int(report[0].split("+")[1].split()[0])},
        parent_publication_id=active.manifest.publication_id,
    )
    parent = manifest.parent_publication_id
    print(f"\npublished {manifest.publication_id} (parent {parent})")
    print("Now restart the API so its writer reloads this generation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
