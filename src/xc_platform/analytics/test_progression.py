from __future__ import annotations

import sqlite3

from xc_platform.analytics.progression import get_athlete_progression
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def _seed_result(
    historical: HistoricalCanonicalWriter,
    *,
    source_id: str,
    ingest_run_id: str,
    athlete_id: str,
    school_id: str,
    season_year: int,
    finish_time_ms: int,
    place: int,
    distance_meters: int | None = 5000,
) -> None:
    meet_id = historical.get_or_create_meet(
        season_year=season_year, meet_number=1, name="Meet 1", series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id,
        division_code="Varsity",
        gender_code="F",
        distance_meters=distance_meters,
    )
    historical.insert_result(
        source_id=source_id,
        race_id=race_id,
        athlete_id=athlete_id,
        school_id=school_id,
        ingest_run_id=ingest_run_id,
        finish_time_ms=finish_time_ms,
        original_time_text=None,
        place_overall=place,
        bib=None,
        grade=None,
        scored_flag="scored",
    )


def test_progression_with_no_results_has_no_trend(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    athlete_id = write.create_athlete(display_name="Jane Doe")

    progression = get_athlete_progression(canonical, athlete_id)
    assert progression.entries == []
    assert progression.pace_trend is None


def test_progression_computes_pace_and_a_three_point_trend(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    historical = HistoricalCanonicalWriter(conn)
    source_id = StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    athlete_id = write.create_athlete(display_name="Jane Doe")
    school_id = write.create_school(canonical_name="St Agnes")

    # 5000m in 24, 23, 22 minutes across three seasons -- steadily improving.
    for season_year, minutes in ((2024, 24), (2025, 23), (2026, 22)):
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=school_id,
            season_year=season_year,
            finish_time_ms=minutes * 60_000,
            place=1,
        )

    progression = get_athlete_progression(canonical, athlete_id)
    assert len(progression.entries) == 3
    assert progression.pace_trend is not None
    assert progression.pace_trend.has_confidence is True
    assert progression.pace_trend.slope_per_x is not None
    assert progression.pace_trend.slope_per_x < 0  # pace improving (dropping)


def test_a_result_with_no_distance_is_excluded_from_the_pace_trend(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    historical = HistoricalCanonicalWriter(conn)
    source_id = StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    athlete_id = write.create_athlete(display_name="Jane Doe")
    school_id = write.create_school(canonical_name="St Agnes")

    _seed_result(
        historical,
        source_id=source_id,
        ingest_run_id=ingest_run_id,
        athlete_id=athlete_id,
        school_id=school_id,
        season_year=2026,
        finish_time_ms=24 * 60_000,
        place=1,
        distance_meters=None,
    )

    progression = get_athlete_progression(canonical, athlete_id)
    assert len(progression.entries) == 1
    assert progression.entries[0].pace_seconds_per_mile is None
    assert progression.pace_trend is None
