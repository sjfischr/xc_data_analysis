from __future__ import annotations

import sqlite3

from xc_platform.analytics.team_scores import (
    get_saint_sebastian_standings,
    get_team_scores,
)
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
    meet_number: int,
    division_code: str = "Varsity",
    gender_code: str = "F",
    finish_time_ms: int,
    place: int | None,
) -> None:
    meet_id = historical.get_or_create_meet(
        season_year=season_year, meet_number=meet_number, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id,
        division_code=division_code,
        gender_code=gender_code,
        distance_meters=5000,
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


def _setup(
    conn: sqlite3.Connection,
) -> tuple[
    CanonicalReadRepository,
    CanonicalWriteRepository,
    HistoricalCanonicalWriter,
    str,
    str,
]:
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
    return canonical, write, historical, source_id, ingest_run_id


def test_team_scores_scores_only_the_qualifying_school_with_5_finishers(
    conn: sqlite3.Connection,
) -> None:
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)

    qualifying_school = write.create_school(canonical_name="St Agnes")
    for place in range(1, 6):
        athlete_id = write.create_athlete(display_name=f"Qualifier {place}")
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=qualifying_school,
            season_year=2026,
            meet_number=1,
            finish_time_ms=1_200_000 + place * 1000,
            place=place,
        )

    short_school = write.create_school(canonical_name="Holy Family")
    for place in range(6, 10):
        athlete_id = write.create_athlete(display_name=f"Short {place}")
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=short_school,
            season_year=2026,
            meet_number=1,
            finish_time_ms=1_300_000 + place * 1000,
            place=place,
        )

    scores = get_team_scores(canonical, season_year=2026)

    assert len(scores) == 1
    entry = scores[0]
    assert entry.school_display_name == "St Agnes"
    assert entry.score == 15
    assert entry.scoring_runners == 5
    assert entry.team_rank == 1


def test_team_scores_filters_by_season_division_gender(
    conn: sqlite3.Connection,
) -> None:
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)
    school_id = write.create_school(canonical_name="St Agnes")

    for season_year in (2025, 2026):
        for place in range(1, 6):
            athlete_id = write.create_athlete(
                display_name=f"{season_year} Runner {place}"
            )
            _seed_result(
                historical,
                source_id=source_id,
                ingest_run_id=ingest_run_id,
                athlete_id=athlete_id,
                school_id=school_id,
                season_year=season_year,
                meet_number=1,
                finish_time_ms=1_200_000 + place * 1000,
                place=place,
            )

    all_scores = get_team_scores(canonical)
    assert {s.season_year for s in all_scores} == {2025, 2026}

    filtered = get_team_scores(canonical, season_year=2026)
    assert len(filtered) == 1
    assert filtered[0].season_year == 2026


def test_saint_sebastian_excludes_an_athlete_who_missed_a_season_meet(
    conn: sqlite3.Connection,
) -> None:
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)
    school_id = write.create_school(canonical_name="St Agnes")

    always_runs = write.create_athlete(display_name="Always Runs")
    _seed_result(
        historical,
        source_id=source_id,
        ingest_run_id=ingest_run_id,
        athlete_id=always_runs,
        school_id=school_id,
        season_year=2026,
        meet_number=1,
        finish_time_ms=500_000,
        place=1,
    )
    _seed_result(
        historical,
        source_id=source_id,
        ingest_run_id=ingest_run_id,
        athlete_id=always_runs,
        school_id=school_id,
        season_year=2026,
        meet_number=2,
        finish_time_ms=490_000,
        place=1,
    )

    misses_one = write.create_athlete(display_name="Misses One Meet")
    _seed_result(
        historical,
        source_id=source_id,
        ingest_run_id=ingest_run_id,
        athlete_id=misses_one,
        school_id=school_id,
        season_year=2026,
        meet_number=1,
        finish_time_ms=510_000,
        place=2,
    )
    # No meet 2 result for "Misses One Meet" -- ineligible for the season.

    standings = get_saint_sebastian_standings(canonical, season_year=2026)

    assert [s.athlete_display_name for s in standings] == ["Always Runs"]
    assert standings[0].meets_run == 2
    assert standings[0].standing_rank == 1


def test_saint_sebastian_filters_by_season_division_gender(
    conn: sqlite3.Connection,
) -> None:
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)
    school_id = write.create_school(canonical_name="St Agnes")

    for season_year in (2025, 2026):
        athlete_id = write.create_athlete(display_name=f"Runner {season_year}")
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=school_id,
            season_year=season_year,
            meet_number=1,
            finish_time_ms=500_000,
            place=1,
        )

    all_standings = get_saint_sebastian_standings(canonical)
    assert {s.season_year for s in all_standings} == {2025, 2026}

    filtered = get_saint_sebastian_standings(canonical, season_year=2026)
    assert len(filtered) == 1
    assert filtered[0].season_year == 2026


def test_team_scores_filtered_by_school_id_keeps_the_correct_rank(
    conn: sqlite3.Connection,
) -> None:
    """Regression test: team_rank is a window function over ALL schools in
    a race. Filtering by school_id must not be applied inside that same
    window (it would leave one row per partition and every rank would
    silently compute as 1) -- the second-place school must still show
    team_rank == 2, not 1, once its own filtered results are the only ones
    returned (Task 14.3, 2026-09-23)."""
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)

    first_place_school = write.create_school(canonical_name="St Agnes")
    for place in range(1, 6):
        athlete_id = write.create_athlete(display_name=f"Fast {place}")
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=first_place_school,
            season_year=2026,
            meet_number=1,
            finish_time_ms=1_200_000 + place * 1000,
            place=place,
        )

    second_place_school = write.create_school(canonical_name="Holy Family")
    for place in range(6, 11):
        athlete_id = write.create_athlete(display_name=f"Slower {place}")
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=second_place_school,
            season_year=2026,
            meet_number=1,
            finish_time_ms=1_400_000 + place * 1000,
            place=place,
        )

    unfiltered = get_team_scores(canonical, season_year=2026)
    assert len(unfiltered) == 2
    ranks_by_school = {s.school_id: s.team_rank for s in unfiltered}
    assert ranks_by_school[first_place_school] == 1
    assert ranks_by_school[second_place_school] == 2

    filtered = get_team_scores(
        canonical, season_year=2026, school_id=second_place_school
    )
    assert len(filtered) == 1
    assert filtered[0].team_rank == 2


def test_list_athletes_for_school_returns_roster_entries(
    conn: sqlite3.Connection,
) -> None:
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)
    school_id = write.create_school(canonical_name="St Agnes")
    other_school_id = write.create_school(canonical_name="Holy Family")

    athlete_id = write.create_athlete(display_name="Jane Doe")
    _seed_result(
        historical,
        source_id=source_id,
        ingest_run_id=ingest_run_id,
        athlete_id=athlete_id,
        school_id=school_id,
        season_year=2026,
        meet_number=1,
        finish_time_ms=500_000,
        place=1,
    )
    historical.upsert_athlete_season(
        athlete_id=athlete_id,
        season_year=2026,
        school_id=school_id,
        grade=6,
        gender_code="F",
    )
    other_athlete_id = write.create_athlete(display_name="Not On This Roster")
    _seed_result(
        historical,
        source_id=source_id,
        ingest_run_id=ingest_run_id,
        athlete_id=other_athlete_id,
        school_id=other_school_id,
        season_year=2026,
        meet_number=1,
        finish_time_ms=510_000,
        place=2,
    )
    historical.upsert_athlete_season(
        athlete_id=other_athlete_id,
        season_year=2026,
        school_id=other_school_id,
        grade=6,
        gender_code="F",
    )

    roster = canonical.list_athletes_for_school(school_id)

    assert len(roster) == 1
    assert roster[0].athlete_id == athlete_id
    assert roster[0].athlete_display_name == "Jane Doe"
    assert roster[0].season_year == 2026


def test_list_athletes_for_school_filters_by_season_year(
    conn: sqlite3.Connection,
) -> None:
    canonical, write, historical, source_id, ingest_run_id = _setup(conn)
    school_id = write.create_school(canonical_name="St Agnes")

    for season_year in (2025, 2026):
        athlete_id = write.create_athlete(display_name=f"Runner {season_year}")
        _seed_result(
            historical,
            source_id=source_id,
            ingest_run_id=ingest_run_id,
            athlete_id=athlete_id,
            school_id=school_id,
            season_year=season_year,
            meet_number=1,
            finish_time_ms=500_000,
            place=1,
        )
        historical.upsert_athlete_season(
            athlete_id=athlete_id,
            season_year=season_year,
            school_id=school_id,
            grade=6,
            gender_code="F",
        )

    all_roster = canonical.list_athletes_for_school(school_id)
    assert {r.season_year for r in all_roster} == {2025, 2026}

    filtered = canonical.list_athletes_for_school(school_id, season_year=2026)
    assert len(filtered) == 1
    assert filtered[0].season_year == 2026
