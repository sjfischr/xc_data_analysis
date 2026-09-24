"""Merge a duplicate school into another, starting from the live data
(2026-09-24). Used when one school was recorded under two names -- first
for "St. Rita Parish Alexandria" (2023) and "St Rita" (2024 on).

Downloads and verifies the active snapshot into WORK_DIR, moves every
result, roster row, and alias from the LOSER school to the WINNER, marks
the loser merged, and records an audited decision. Nothing is published:
check the result, then publish WORK_DIR/xc.db with
``scripts/publish_snapshot.py`` (dry run first, then ``--yes``), and
restart the API.

Usage::

    python scripts/merge_schools.py <work-dir> <bucket> --winner "St Rita" \\
        --loser "St. Rita Parish Alexandria" --actor you@example.com
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xc_platform.db.publication.hydration import hydrate_writer_database
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import Boto3S3Client
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.resolution.corrections import merge_schools

SEASON_COUNTS = (
    "SELECT m.season_year, COUNT(*) FROM results r "
    "JOIN races ra ON ra.race_id = r.race_id "
    "JOIN meets m ON m.meet_id = ra.meet_id WHERE r.school_id = ? GROUP BY 1"
)


def _key(name: str) -> str:
    # Source names can carry non-breaking spaces ("St. Rita Parish\xa0Alexandria").
    return " ".join(name.replace("\xa0", " ").split()).casefold()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("work_dir")
    parser.add_argument("bucket")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--winner", required=True, help="school that keeps its name")
    parser.add_argument("--loser", required=True, help="school folded into it")
    parser.add_argument("--actor", required=True, help="who is making the change")
    parser.add_argument("--reason", default=None)
    args = parser.parse_args()

    work = Path(args.work_dir)
    if (work / "xc.db").exists():
        raise SystemExit(f"{work / 'xc.db'} exists; use an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    s3 = Boto3S3Client.create(bucket=args.bucket, region=args.region)
    conn, manifest = hydrate_writer_database(
        SnapshotReader(s3, cache_dir=work / "cache"), work / "xc.db"
    )
    if manifest is None:
        raise SystemExit("no active publication to start from")
    print(f"live publication: {manifest.publication_id}")

    schools = {
        _key(r["display_name"]): (r["school_id"], r["display_name"])
        for r in conn.execute(
            "SELECT school_id, display_name FROM schools WHERE status = 'active'"
        )
    }
    ids = []
    for name in (args.winner, args.loser):
        if _key(name) not in schools:
            raise SystemExit(f"no active school named {name!r}")
        ids.append(schools[_key(name)])
    (winner_id, winner_name), (loser_id, loser_name) = ids
    for label, school_id, name in (
        ("winner", winner_id, winner_name),
        ("loser", loser_id, loser_name),
    ):
        seasons = dict(conn.execute(SEASON_COUNTS, (school_id,)).fetchall())
        print(f"{label}: {name!r} results by season {seasons}")

    reason = args.reason or (
        f"Owner correction: {loser_name!r} and {winner_name!r} are the same school."
    )
    decision_id = merge_schools(
        ResolutionRepository(conn),
        CanonicalWriteRepository(conn),
        winner_school_id=winner_id,
        loser_school_id=loser_id,
        actor=args.actor,
        reason=reason,
    )
    after = dict(conn.execute(SEASON_COUNTS, (winner_id,)).fetchall())
    active = conn.execute(
        "SELECT COUNT(*) FROM schools WHERE status = 'active'"
    ).fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    print(f"merged; decision {decision_id}")
    print(f"{winner_name!r} results by season now {after}")
    print(f"active schools: {active}; integrity_check: {integrity}")
    if integrity != "ok":
        return 1
    print(f"\nNext: python scripts/publish_snapshot.py {work / 'xc.db'} {args.bucket}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
