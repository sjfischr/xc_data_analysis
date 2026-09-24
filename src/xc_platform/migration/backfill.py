"""Orchestrate the one-time historical backfill (Task 5.1).

:func:`run_backfill` reads the frozen baseline CSV once, creates a migration
ingest run, stages every row (Requirement 7.1), quarantines rows that cannot
become an individual result (Requirement 1.7, 1.10), and promotes every
other row directly into the canonical schema built in Task 4.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.aliases import resolve_team_name
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter
from xc_platform.migration.historical_csv import HistoricalRow, iter_rows

HISTORICAL_SOURCE_NAMESPACE = "historical_csv:season_results"


class FrozenBaselineChangedError(RuntimeError):
    """The CSV's content hash no longer matches the expected frozen value."""


@dataclass(frozen=True, slots=True)
class QuarantinedRow:
    row_index: int
    staged_result_id: str
    reason: str


@dataclass(slots=True)
class BackfillReport:
    ingest_run_id: str
    source_id: str
    content_sha256: str
    total_rows: int = 0
    inserted_results: int = 0
    quarantined: list[QuarantinedRow] = field(default_factory=list)
    meets_created: int = 0
    races_created: int = 0
    schools_created: int = 0
    athletes_created: int = 0


def sha256_of_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_fields_json(row: HistoricalRow) -> str:
    return json.dumps(
        {
            "season_year": row.season_year,
            "meet_number": row.meet_number,
            "division": row.division,
            "gender_code": row.gender_code,
            "distance_meters": row.distance_meters,
            "athlete_full_name": row.athlete_full_name,
            "team_name": row.team_name,
            "bib": row.bib,
            "grade": row.grade,
            "place_overall": row.place_overall,
            "finish_time_ms": row.finish_time_ms,
            "original_time_text": row.original_time_text,
            "scored_flag": row.scored_flag,
        }
    )


def run_backfill(
    conn: sqlite3.Connection,
    csv_path: str | Path,
    *,
    correlation_id: str,
    expected_sha256: str | None = None,
) -> BackfillReport:
    """Migrate every row of the frozen baseline CSV into the canonical schema.

    Raises :class:`FrozenBaselineChangedError` if ``expected_sha256`` is
    given and does not match the file's actual content hash -- the frozen
    baseline (Requirement 1.8) must not have changed since it was fingerprinted.
    """
    content_sha256 = sha256_of_file(csv_path)
    if expected_sha256 is not None and content_sha256 != expected_sha256:
        raise FrozenBaselineChangedError(
            f"{csv_path}: sha256 {content_sha256} does not match the expected "
            f"frozen value {expected_sha256}. The historical baseline must not "
            "change (Requirement 1.8); re-freeze it deliberately if this is "
            "intentional."
        )

    staging = StagingRepository(conn)
    ingest = IngestRunRepository(conn)
    writer = HistoricalCanonicalWriter(conn)

    source_id = staging.get_or_create_source(
        source_namespace=HISTORICAL_SOURCE_NAMESPACE,
        adapter_type="historical_csv",
        base_domain="local",
    )
    run_id = ingest.create_run(
        source_id=source_id,
        submitted_url=str(Path(csv_path).as_posix()),
        adapter_type="historical_csv",
        correlation_id=correlation_id,
    )
    ingest.transition(run_id, "discovering")

    byte_size = Path(csv_path).stat().st_size
    source_object_id = staging.record_source_object(
        source_id=source_id,
        ingest_run_id=run_id,
        source_url=str(Path(csv_path).as_posix()),
        raw_s3_key="",
        content_sha256=content_sha256,
        byte_size=byte_size,
        media_type="text/csv",
        extraction_strategy="historical_csv",
    )

    ingest.transition(run_id, "staging")

    report = BackfillReport(
        ingest_run_id=run_id, source_id=source_id, content_sha256=content_sha256
    )

    meet_ids: dict[tuple[int, int], str] = {}
    race_ids: dict[tuple[str, str, str], str] = {}
    school_ids: dict[str, str] = {}
    athlete_ids: dict[str, str] = {}

    for row in iter_rows(csv_path):
        report.total_rows += 1
        staged_id = staging.stage_result(
            ingest_run_id=run_id,
            source_object_id=source_object_id,
            source_id=source_id,
            idempotency_key=f"historical-row:{row.row_index}",
            raw_fields_json=json.dumps(row.raw, ensure_ascii=False),
            candidate_fields_json=_candidate_fields_json(row),
        )

        if row.quarantine_reason is not None:
            staging.quarantine(staged_id, reason=row.quarantine_reason)
            report.quarantined.append(
                QuarantinedRow(
                    row_index=row.row_index,
                    staged_result_id=staged_id,
                    reason=row.quarantine_reason,
                )
            )
            continue

        if (
            row.season_year is None
            or row.meet_number is None
            or row.division is None
            or row.gender_code is None
            or row.athlete_full_name is None
        ):
            raise RuntimeError(
                f"row {row.row_index} has quarantine_reason=None but a "
                "required field is missing; historical_csv.parse_row should "
                "have quarantined it. This indicates a bug in parse_row, "
                "not a data problem."
            )

        meet_key = (row.season_year, row.meet_number)
        meet_id = meet_ids.get(meet_key)
        if meet_id is None:
            meet_id = writer.get_or_create_meet(
                season_year=row.season_year,
                meet_number=row.meet_number,
                name=row.meet_name,
                series=row.meet_series,
            )
            meet_ids[meet_key] = meet_id
            report.meets_created += 1

        race_key = (meet_id, row.division, row.gender_code)
        race_id = race_ids.get(race_key)
        if race_id is None:
            race_id = writer.get_or_create_race(
                meet_id=meet_id,
                division_code=row.division,
                gender_code=row.gender_code,
                distance_meters=row.distance_meters,
            )
            race_ids[race_key] = race_id
            report.races_created += 1

        school_cache_key = row.team_name or "\x00unknown\x00"
        school_id = school_ids.get(school_cache_key)
        if school_id is None:
            school_id = resolve_team_name(writer, row)
            school_ids[school_cache_key] = school_id
            report.schools_created += 1

        athlete_id = athlete_ids.get(row.athlete_full_name)
        if athlete_id is None:
            athlete_id = writer.get_or_create_athlete(row.athlete_full_name)
            athlete_ids[row.athlete_full_name] = athlete_id
            report.athletes_created += 1

        writer.upsert_athlete_season(
            athlete_id=athlete_id,
            season_year=row.season_year,
            school_id=school_id,
            grade=row.grade,
            gender_code=row.gender_code,
        )

        writer.insert_result(
            source_id=source_id,
            race_id=race_id,
            athlete_id=athlete_id,
            school_id=school_id,
            ingest_run_id=run_id,
            finish_time_ms=row.finish_time_ms,
            original_time_text=row.original_time_text,
            place_overall=row.place_overall,
            bib=row.bib,
            grade=row.grade,
            scored_flag=row.scored_flag,
        )
        staging.mark_committed(staged_id)
        report.inserted_results += 1

    ingest.record_counts(
        run_id, inserted=report.inserted_results, quarantined=len(report.quarantined)
    )
    ingest.transition(run_id, "awaiting_review")
    return report
