from __future__ import annotations

import sqlite3

from xc_platform.agents.tools import build_analytics_tools
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def _seed_one_school_five_finishers(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
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
    school_id = write.create_school(canonical_name="St Agnes")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    for place in range(1, 6):
        athlete_id = write.create_athlete(display_name=f"Runner {place}")
        historical.insert_result(
            source_id=source_id,
            race_id=race_id,
            athlete_id=athlete_id,
            school_id=school_id,
            ingest_run_id=ingest_run_id,
            finish_time_ms=1_200_000 + place * 1000,
            original_time_text=None,
            place_overall=place,
            bib=None,
            grade=None,
            scored_flag="scored",
        )


def test_get_team_scores_tool_returns_real_data(conn: sqlite3.Connection) -> None:
    _seed_one_school_five_finishers(conn)
    canonical = CanonicalReadRepository(conn)
    tools = {
        t.tool_name: t for t in build_analytics_tools(canonical, publication_id="pub-1")
    }

    result = tools["get_team_scores_tool"](season_year=2026)

    assert result["publication_id"] == "pub-1"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["school_display_name"] == "St Agnes"
    assert result["entries"][0]["score"] == 15


def test_get_team_scores_tool_with_no_filters_still_works(
    conn: sqlite3.Connection,
) -> None:
    _seed_one_school_five_finishers(conn)
    canonical = CanonicalReadRepository(conn)
    tools = {
        t.tool_name: t for t in build_analytics_tools(canonical, publication_id="pub-1")
    }

    result = tools["get_team_scores_tool"]()

    assert len(result["entries"]) == 1


def test_get_saint_sebastian_standings_tool_returns_real_data(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
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
    school_id = write.create_school(canonical_name="St Agnes")
    athlete_id = write.create_athlete(display_name="Always Runs")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    historical.insert_result(
        source_id=source_id,
        race_id=race_id,
        athlete_id=athlete_id,
        school_id=school_id,
        ingest_run_id=ingest_run_id,
        finish_time_ms=500_000,
        original_time_text=None,
        place_overall=1,
        bib=None,
        grade=None,
        scored_flag="scored",
    )
    canonical = CanonicalReadRepository(conn)
    tools = {
        t.tool_name: t for t in build_analytics_tools(canonical, publication_id="pub-1")
    }

    result = tools["get_saint_sebastian_standings_tool"](season_year=2026)

    assert result["publication_id"] == "pub-1"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["athlete_display_name"] == "Always Runs"
    assert result["entries"][0]["standing_rank"] == 1
