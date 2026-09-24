"""Head-to-head comparison and what-if team scoring (Task 19.2 -- design.md
11.1's "comparisons" and "what-if scenarios", previously unbuilt because no
business rule existed; the rules below are now that definition).

**Comparison**: per-athlete summaries plus every race the athletes shared,
with a head-to-head record (who finished ahead, by overall place, falling
back to finish time when a place is missing).

**What-if team scoring**: re-scores one race after removing runners and/or
adding hypothetical runners at a given finish time. The scoring rule is
exactly ``v_team_scores`` (migrations/0003_analytics_views.sql, parity-
tested): overall places, each school's five best placed finishers, only
schools with at least five placed finishers score, and the reserved
"Unknown" school never fields a team. Removing a runner moves everyone
behind them up one place; adding one moves everyone slower down one place.
Results are clearly a scenario, never written anywhere.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from xc_platform.analytics.dashboard import (
    AthleteProfile,
    ResultFilters,
    get_athlete_profile,
    list_results,
)
from xc_platform.db.repositories.canonical import CanonicalReadRepository

UNKNOWN_SCHOOL_CANONICAL_NAME = "Unknown"
SCORING_RUNNERS = 5


@dataclass(frozen=True, slots=True)
class SharedRaceEntry:
    athlete_id: str
    place_overall: int | None
    finish_time_ms: int | None
    pace_seconds_per_mile: float | None


@dataclass(frozen=True, slots=True)
class SharedRace:
    race_id: str
    season_year: int
    meet_number: int | None
    meet_name: str
    division_code: str
    gender_code: str
    entries: list[SharedRaceEntry]


@dataclass(frozen=True, slots=True)
class HeadToHead:
    athlete_a_id: str
    athlete_b_id: str
    races: int
    a_ahead: int
    b_ahead: int


@dataclass(frozen=True, slots=True)
class Comparison:
    profiles: list[AthleteProfile]
    missing_athlete_ids: list[str]
    shared_races: list[SharedRace]
    head_to_head: list[HeadToHead]


def _ahead(a: SharedRaceEntry, b: SharedRaceEntry) -> int:
    """1 if ``a`` beat ``b``, -1 if ``b`` beat ``a``, 0 if undecidable."""
    if a.place_overall is not None and b.place_overall is not None:
        return (a.place_overall < b.place_overall) - (a.place_overall > b.place_overall)
    if a.finish_time_ms is not None and b.finish_time_ms is not None:
        return (a.finish_time_ms < b.finish_time_ms) - (
            a.finish_time_ms > b.finish_time_ms
        )
    return 0


def compare_athletes(
    canonical: CanonicalReadRepository, athlete_ids: list[str]
) -> Comparison:
    profiles: list[AthleteProfile] = []
    missing: list[str] = []
    for athlete_id in dict.fromkeys(athlete_ids):
        profile = get_athlete_profile(canonical, athlete_id)
        if profile is None:
            missing.append(athlete_id)
        else:
            profiles.append(profile)

    by_race: dict[str, dict[str, SharedRaceEntry]] = defaultdict(dict)
    race_context = {}
    for profile in profiles:
        for r in profile.results:
            by_race[r.row.race_id][profile.athlete.athlete_id] = SharedRaceEntry(
                athlete_id=profile.athlete.athlete_id,
                place_overall=r.row.place_overall,
                finish_time_ms=r.row.finish_time_ms,
                pace_seconds_per_mile=r.pace_seconds_per_mile,
            )
            race_context[r.row.race_id] = r.row

    shared = [
        SharedRace(
            race_id=race_id,
            season_year=race_context[race_id].season_year,
            meet_number=race_context[race_id].meet_number,
            meet_name=race_context[race_id].meet_name,
            division_code=race_context[race_id].division_code,
            gender_code=race_context[race_id].gender_code,
            entries=list(entries.values()),
        )
        for race_id, entries in by_race.items()
        if len(entries) >= 2
    ]
    shared.sort(key=lambda s: (s.season_year, s.meet_number or 0))

    records: list[HeadToHead] = []
    ids = [p.athlete.athlete_id for p in profiles]
    for a_id, b_id in combinations(ids, 2):
        races = a_ahead = b_ahead = 0
        for race in shared:
            entries = {e.athlete_id: e for e in race.entries}
            if a_id in entries and b_id in entries:
                races += 1
                outcome = _ahead(entries[a_id], entries[b_id])
                a_ahead += outcome == 1
                b_ahead += outcome == -1
        records.append(
            HeadToHead(
                athlete_a_id=a_id,
                athlete_b_id=b_id,
                races=races,
                a_ahead=a_ahead,
                b_ahead=b_ahead,
            )
        )
    return Comparison(
        profiles=profiles,
        missing_athlete_ids=missing,
        shared_races=shared,
        head_to_head=records,
    )


@dataclass(frozen=True, slots=True)
class HypotheticalRunner:
    school_id: str
    finish_time_ms: int
    label: str = "hypothetical runner"


@dataclass(frozen=True, slots=True)
class ScenarioTeamScore:
    school_id: str
    school_display_name: str
    score: int
    scoring_places: list[int]
    team_rank: int


@dataclass
class TeamScenario:
    season_year: int
    meet_number: int
    division_code: str
    gender_code: str
    actual: list[ScenarioTeamScore]
    scenario: list[ScenarioTeamScore]
    removed_athlete_ids: list[str] = field(default_factory=list)
    unknown_athlete_ids: list[str] = field(default_factory=list)
    added: list[HypotheticalRunner] = field(default_factory=list)
    placed_runners: int = 0


@dataclass(frozen=True, slots=True)
class _Runner:
    school_id: str
    time_ms: int | None
    place: int | None
    is_unknown_school: bool


def _score(runners: list[_Runner], names: dict[str, str]) -> list[ScenarioTeamScore]:
    places_by_school: dict[str, list[int]] = defaultdict(list)
    for place, runner in enumerate(runners, start=1):
        if not runner.is_unknown_school:
            places_by_school[runner.school_id].append(place)
    teams = [
        (school_id, sorted(places)[:SCORING_RUNNERS])
        for school_id, places in places_by_school.items()
        if len(places) >= SCORING_RUNNERS
    ]
    teams.sort(key=lambda t: (sum(t[1]), names.get(t[0], t[0])))
    return [
        ScenarioTeamScore(
            school_id=school_id,
            school_display_name=names.get(school_id, school_id),
            score=sum(places),
            scoring_places=places,
            team_rank=rank,
        )
        for rank, (school_id, places) in enumerate(teams, start=1)
    ]


def team_score_scenario(
    canonical: CanonicalReadRepository,
    *,
    season_year: int,
    meet_number: int,
    division_code: str,
    gender_code: str,
    remove_athlete_ids: tuple[str, ...] = (),
    add_runners: tuple[HypotheticalRunner, ...] = (),
) -> TeamScenario:
    rows = list_results(
        canonical,
        ResultFilters(
            season_year=season_year,
            meet_numbers=(meet_number,),
            division_code=division_code,
            gender_code=gender_code,
        ),
    )
    schools = canonical.list_schools()
    names = {s.school_id: s.display_name for s in schools}
    unknown_ids = {
        s.school_id
        for s in schools
        if s.canonical_name == UNKNOWN_SCHOOL_CANONICAL_NAME
    }

    placed = sorted(
        (r for r in rows if r.row.place_overall is not None),
        key=lambda r: r.row.place_overall or 0,
    )
    actual_runners = [
        _Runner(
            school_id=r.row.school_id,
            time_ms=r.row.finish_time_ms,
            place=r.row.place_overall,
            is_unknown_school=r.row.school_id in unknown_ids,
        )
        for r in placed
    ]
    present = {r.row.athlete_id for r in placed}
    removed = [a for a in remove_athlete_ids if a in present]
    unknown = [a for a in remove_athlete_ids if a not in present]

    kept = [
        _Runner(
            school_id=r.row.school_id,
            time_ms=r.row.finish_time_ms,
            place=r.row.place_overall,
            is_unknown_school=r.row.school_id in unknown_ids,
        )
        for r in placed
        if r.row.athlete_id not in removed
    ]
    scenario_runners = list(kept)
    for extra in add_runners:
        # Insert before the first runner with a slower recorded time; a
        # runner without a time keeps their recorded place order.
        index = next(
            (
                i
                for i, r in enumerate(scenario_runners)
                if r.time_ms is not None and r.time_ms > extra.finish_time_ms
            ),
            len(scenario_runners),
        )
        scenario_runners.insert(
            index,
            _Runner(
                school_id=extra.school_id,
                time_ms=extra.finish_time_ms,
                place=None,
                is_unknown_school=extra.school_id in unknown_ids,
            ),
        )

    # The actual race is re-scored by the same function the scenario uses,
    # so any difference between the two is caused only by the changes.
    return TeamScenario(
        season_year=season_year,
        meet_number=meet_number,
        division_code=division_code,
        gender_code=gender_code,
        actual=_score(actual_runners, names),
        scenario=_score(scenario_runners, names),
        removed_athlete_ids=removed,
        unknown_athlete_ids=unknown,
        added=list(add_runners),
        placed_runners=len(scenario_runners),
    )
