"""Comparison and what-if team scoring (Task 19.2). The what-if engine's
re-score of an unchanged race must equal the official ``v_team_scores``;
on the real 2023-2025 data it does for all 47 scored races (checked
2026-09-23), and these tests pin the rule on small races."""

from __future__ import annotations

import sqlite3

from xc_platform.analytics.scenarios import (
    HypotheticalRunner,
    compare_athletes,
    team_score_scenario,
)
from xc_platform.analytics.team_scores import get_team_scores
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


class _Seeder:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.write = CanonicalWriteRepository(conn)
        self.historical = HistoricalCanonicalWriter(conn)
        self.source_id = StagingRepository(conn).get_or_create_source(
            source_namespace="runsignup",
            adapter_type="runsignup",
            base_domain="runsignup.com",
        )
        self.run_id = IngestRunRepository(conn).create_run(
            source_id=self.source_id,
            submitted_url="https://runsignup.com/Race/Results/1",
            adapter_type="runsignup",
            correlation_id="c",
        )

    def race(self, meet_number: int = 1) -> str:
        meet_id = self.historical.get_or_create_meet(
            season_year=2026, meet_number=meet_number, name=None, series="NVJCYO"
        )
        return self.historical.get_or_create_race(
            meet_id=meet_id, division_code="JV", gender_code="F", distance_meters=3000
        )

    def result(self, race_id: str, athlete_id: str, school_id: str, place: int) -> None:
        self.historical.insert_result(
            source_id=self.source_id,
            race_id=race_id,
            athlete_id=athlete_id,
            school_id=school_id,
            ingest_run_id=self.run_id,
            finish_time_ms=600_000 + place * 1000,
            original_time_text=None,
            place_overall=place,
            bib=None,
            grade=None,
            scored_flag="scored",
        )


def _two_team_race(conn: sqlite3.Connection) -> tuple[str, str, list[str]]:
    """School A takes places 1,3,5,7,9 (25); School B 2,4,6,8,10 (30)."""
    s = _Seeder(conn)
    race = s.race()
    a = s.write.create_school(canonical_name="School A")
    b = s.write.create_school(canonical_name="School B")
    a_runners = []
    for place in range(1, 11):
        athlete = s.write.create_athlete(display_name=f"Runner {place}")
        school = a if place % 2 else b
        if school == a:
            a_runners.append(athlete)
        s.result(race, athlete, school, place)
    return a, b, a_runners


def test_unchanged_scenario_equals_the_official_scores(
    conn: sqlite3.Connection,
) -> None:
    _two_team_race(conn)
    canonical = CanonicalReadRepository(conn)
    official = {
        (t.school_id, t.score) for t in get_team_scores(canonical, season_year=2026)
    }
    scenario = team_score_scenario(
        canonical, season_year=2026, meet_number=1, division_code="JV", gender_code="F"
    )
    assert {(t.school_id, t.score) for t in scenario.actual} == official
    assert [t.score for t in scenario.scenario] == [25, 30]


def test_removing_a_runner_moves_everyone_behind_up(conn: sqlite3.Connection) -> None:
    _, b, a_runners = _two_team_race(conn)
    scenario = team_score_scenario(
        CanonicalReadRepository(conn),
        season_year=2026,
        meet_number=1,
        division_code="JV",
        gender_code="F",
        remove_athlete_ids=(a_runners[0], "not-in-race"),
    )
    # A drops below five finishers and stops scoring; B's places shift up
    # by one: 1,3,5,7,9 = 25.
    assert [(t.school_id, t.score) for t in scenario.scenario] == [(b, 25)]
    assert scenario.unknown_athlete_ids == ["not-in-race"]


def test_adding_a_fast_runner_pushes_slower_runners_down(
    conn: sqlite3.Connection,
) -> None:
    a, b, _ = _two_team_race(conn)
    scenario = team_score_scenario(
        CanonicalReadRepository(conn),
        season_year=2026,
        meet_number=1,
        division_code="JV",
        gender_code="F",
        add_runners=(HypotheticalRunner(school_id=b, finish_time_ms=500_000),),
    )
    # B's new runner wins: B = 1,3,5,7,9 = 25; A = 2,4,6,8,10 = 30.
    assert [(t.school_id, t.score) for t in scenario.scenario] == [(b, 25), (a, 30)]


def test_compare_reports_head_to_head_in_shared_races(conn: sqlite3.Connection) -> None:
    s = _Seeder(conn)
    school = s.write.create_school(canonical_name="St Agnes")
    fast = s.write.create_athlete(display_name="Fast Runner")
    slow = s.write.create_athlete(display_name="Slow Runner")
    for meet in (1, 2):
        race = s.race(meet)
        s.result(race, fast, school, 1)
        s.result(race, slow, school, 2)

    comparison = compare_athletes(
        CanonicalReadRepository(conn), [fast, slow, "missing"]
    )

    assert comparison.missing_athlete_ids == ["missing"]
    assert len(comparison.shared_races) == 2
    [record] = comparison.head_to_head
    assert (record.races, record.a_ahead, record.b_ahead) == (2, 2, 0)
