"""Load meet dates, venues, race start times, and race-day weather from
RunSignup into a local copy of the canonical database (2026-10-18).

Like scripts/merge_schools.py, it downloads and verifies the active
snapshot into WORK_DIR/xc.db (or works on an existing file with --db), makes
its change there, and publishes nothing: check the summary, then publish
WORK_DIR/xc.db with scripts/publish_snapshot.py (dry run first, then --yes)
and restart the API. Pending migrations are applied (0004 adds the weather
table), so publish only after the API and agent run code that knows
migration 0004 -- a reader refuses a newer schema.

For every meet race in the adapter's MEET_RACE_IDS it reads the results
Overview page (for each season's raceEventDaysId), the race's REST metadata
(for the venue address), and the weather feed, writing each raw response to
--raw-dir. Re-running is safe: each meet's weather rows are replaced.

Usage::

    python scripts/load_race_weather.py <work-dir> <bucket> [--upload-raw]
        [--raw-dir DIR] [--region us-east-1]
    python scripts/load_race_weather.py --db <existing.db> [--raw-dir DIR]

--upload-raw archives each raw response under
s3://<bucket>/raw/runsignup-weather/<season>/ and records the key.

TLS goes through the CA bundle in XC_CA_BUNDLE or AWS_CA_BUNDLE when set
(Norton inspects TLS on the owner's machine).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.publication.hydration import hydrate_writer_database
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import Boto3S3Client
from xc_platform.ingest.adapters.runsignup import MEET_RACE_IDS
from xc_platform.ingest.weather import (
    OVERVIEW_URL,
    WEATHER_URL,
    apply_meet_weather,
    meet_days_from_overview,
    parse_weather,
    superseded,
)

ALLOWED_HOST = "runsignup.com"
PAUSE_SECONDS = 0.5  # be polite: ~20 requests in total


def _context() -> ssl.SSLContext:
    bundle = os.environ.get("XC_CA_BUNDLE") or os.environ.get("AWS_CA_BUNDLE")
    return (
        ssl.create_default_context(cafile=bundle)
        if bundle
        else ssl.create_default_context()
    )


def _get(url: str, ctx: ssl.SSLContext, *, ajax: bool = False) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValueError(
            f"refusing to fetch anything but https://{ALLOWED_HOST}: {url}"
        )
    headers = {"User-Agent": "xc-data-platform weather loader"}
    if ajax:
        headers |= {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    # Scheme and host are checked above, so no file: or other scheme opens.
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    with urllib.request.urlopen(request, context=ctx, timeout=30) as response:  # noqa: S310
        body: bytes = response.read()
    time.sleep(PAUSE_SECONDS)
    return body


def _venue(race_id: str, ctx: ssl.SSLContext) -> str | None:
    meta = json.loads(
        _get(f"https://{ALLOWED_HOST}/Rest/race/{race_id}?format=json", ctx)
    )
    address = (meta.get("race") or {}).get("address") or {}
    parts = [address.get("street"), address.get("city")]
    tail = " ".join(p for p in (address.get("state"), address.get("zipcode")) if p)
    text = ", ".join(p for p in [*parts, tail] if p)
    return text or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("work_dir", type=Path, nargs="?")
    parser.add_argument("bucket", nargs="?")
    parser.add_argument("--db", type=Path, help="load into this file instead")
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--upload-raw", action="store_true")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args(argv)
    if args.db is None and (args.work_dir is None or args.bucket is None):
        parser.error("give <work-dir> <bucket>, or --db <file>")
    if args.upload_raw and args.bucket is None:
        parser.error("--upload-raw needs <bucket>")

    ctx = _context()
    base = args.db.parent if args.db is not None else args.work_dir
    raw_dir: Path = args.raw_dir or base / "race-weather-raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    s3 = None
    if args.upload_raw:
        import boto3

        s3 = boto3.client("s3", region_name=args.region)

    if args.db is not None:
        db_path: Path = args.db
        conn = open_writer_connection(db_path)
        migrate(conn)
    else:
        db_path = args.work_dir / "xc.db"
        if db_path.exists():
            raise SystemExit(f"{db_path} exists; use an empty work directory")
        args.work_dir.mkdir(parents=True, exist_ok=True)
        conn, manifest = hydrate_writer_database(
            SnapshotReader(
                Boto3S3Client.create(bucket=args.bucket, region=args.region),
                cache_dir=args.work_dir / "cache",
            ),
            db_path,
        )
        if manifest is None:
            raise SystemExit("nothing has been published yet")
        print(f"working on publication {manifest.publication_id}")
        migrate(conn)
    problems = 0
    try:
        for race_id in sorted(MEET_RACE_IDS):
            html = _get(OVERVIEW_URL.format(race_id=race_id), ctx).decode("utf-8")
            venue = _venue(race_id, ctx)
            for day in meet_days_from_overview(html, race_id):
                label = f"{day.season_year} Meet {day.meet_number} (race {race_id})"
                if superseded(race_id, day.season_year):
                    print(f"skip  {label}: placeholder; another race holds this meet")
                    continue
                query = urllib.parse.urlencode(
                    {"raceId": race_id, "raceEventDaysId": day.race_event_days_id}
                )
                raw = _get(f"{WEATHER_URL}?{query}", ctx, ajax=True)
                retrieved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                sha = hashlib.sha256(raw).hexdigest()
                stem = f"{race_id}-{day.season_year}-{day.race_event_days_id}"
                name = f"{stem}-{sha[:16]}.json"
                (raw_dir / name).write_bytes(raw)
                key = None
                if s3 is not None:
                    key = f"raw/runsignup-weather/{day.season_year}/{name}"
                    s3.put_object(
                        Bucket=args.bucket,
                        Key=key,
                        Body=raw,
                        ContentType="application/json",
                        ChecksumAlgorithm="SHA256",
                    )
                summary = apply_meet_weather(
                    conn,
                    day,
                    parse_weather(json.loads(raw)),
                    venue=venue,
                    raw_sha256=sha,
                    raw_s3_key=key,
                    retrieved_at=retrieved_at,
                )
                if summary.skipped_reason:
                    print(f"skip  {label}: {summary.skipped_reason}")
                    continue
                notes = []
                if summary.events_without_race:
                    notes.append(
                        "no race in the database (never published) for "
                        f"{list(summary.events_without_race)}"
                    )
                if summary.unparsed_events:
                    notes.append(f"UNPARSED events {list(summary.unparsed_events)}")
                    problems += 1
                print(
                    f"ok    {label}: {summary.hours_written} hours, "
                    f"{summary.races_timed} races timed"
                    + "".join(f"; {n}" for n in notes)
                )
        conn.commit()
    finally:
        conn.close()
    print(f"\nRaw responses in {raw_dir}")
    if s3 is not None:
        print(f"Archived to s3://{args.bucket}/raw/runsignup-weather/")
    if args.bucket:
        print(f"Next: python scripts/publish_snapshot.py {db_path} {args.bucket}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
