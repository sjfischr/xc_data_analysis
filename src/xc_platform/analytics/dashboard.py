"""Dashboard aggregates (Task 19.1): the legacy Streamlit dashboard's
overview metrics, leaderboards, athlete profile, and school profile,
computed from one flattened result feed
(:meth:`CanonicalReadRepository.list_result_rows`).

Parity notes against ``dashboard.py`` (legacy, still live on Heroku):

* Pace is seconds per mile from the race's recorded distance; speed is mph.
* "Most improved" compares an athlete's first and latest pace within the
  filtered results, in chronological order (season, meet number). Legacy
  sorted by meet number only, which interleaved seasons in "All Seasons"
  mode; chronological order is the intended meaning.
* "Top placements" are places 1-10, best place first, most recent first
  within a tie.

Plain, runtime-neutral functions -- no Strands import, same as
:mod:`xc_platform.analytics.catalog`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from xc_platform.analytics.progression import METERS_PER_MILE
from xc_platform.analytics.trend import TrendResult, compute_trend
from xc_platform.db.repositories.canonical import (
    AthleteRecord,
    CanonicalReadRepository,
    ResultRowRecord,
    SchoolRecord,
    TeamScoreRecord,
)

LEADERBOARD_SIZE = 10


@dataclass(frozen=True, slots=True)
class ResultRow:
    row: ResultRowRecord
    pace_seconds_per_mile: float | None
    speed_mph: float | None


@dataclass(frozen=True, slots=True)
class ResultFilters:
    season_year: int | None = None
    school_id: str | None = None
    athlete_id: str | None = None
    division_code: str | None = None
    gender_code: str | None = None
    meet_numbers: tuple[int, ...] = ()
    grades: tuple[int, ...] = ()
    race_id: str | None = None


@dataclass(frozen=True, slots=True)
class OverviewMetrics:
    athletes: int
    schools: int
    meets: int
    seasons: int
    results: int
    athletes_with_progress: int


@dataclass(frozen=True, slots=True)
class ImprovementEntry:
    athlete_id: str
    athlete_display_name: str
    school_display_name: str
    division_code: str
    races: int
    first_pace_seconds_per_mile: float
    latest_pace_seconds_per_mile: float
    improvement_seconds_per_mile: float
    improvement_pct: float


@dataclass(frozen=True, slots=True)
class Overview:
    metrics: OverviewMetrics
    fastest_pace: list[ResultRow]
    top_placements: list[ResultRow]
    most_improved: list[ImprovementEntry]


@dataclass(frozen=True, slots=True)
class AthleteSeason:
    season_year: int
    school_id: str
    school_display_name: str
    grade: int | None
    gender_code: str
    division_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AthleteSummaryStats:
    races: int
    seasons: int
    best_time_ms: int | None
    best_pace_seconds_per_mile: float | None
    best_place: int | None
    latest_pace_seconds_per_mile: float | None


@dataclass(frozen=True, slots=True)
class AthleteProfile:
    athlete: AthleteRecord
    seasons: list[AthleteSeason]
    summary: AthleteSummaryStats
    results: list[ResultRow]
    pace_trend: TrendResult | None


@dataclass(frozen=True, slots=True)
class SchoolSeason:
    season_year: int
    athletes: int
    results: int


@dataclass(frozen=True, slots=True)
class SchoolProfile:
    school: SchoolRecord
    seasons: list[SchoolSeason]
    team_scores: list[TeamScoreRecord]
    top_athletes: list[ResultRow]


def enrich(row: ResultRowRecord) -> ResultRow:
    pace: float | None = None
    speed: float | None = None
    if row.finish_time_ms and row.distance_meters:
        seconds = row.finish_time_ms / 1000.0
        miles = row.distance_meters / METERS_PER_MILE
        pace = seconds / miles
        speed = miles / (seconds / 3600.0)
    return ResultRow(row=row, pace_seconds_per_mile=pace, speed_mph=speed)


def list_results(
    canonical: CanonicalReadRepository, filters: ResultFilters
) -> list[ResultRow]:
    return [
        enrich(r)
        for r in canonical.list_result_rows(
            season_year=filters.season_year,
            school_id=filters.school_id,
            athlete_id=filters.athlete_id,
            division_code=filters.division_code,
            gender_code=filters.gender_code,
            meet_numbers=filters.meet_numbers,
            grades=filters.grades,
            race_id=filters.race_id,
        )
    ]


def _chronological_key(r: ResultRow) -> tuple[int, int, str]:
    return (r.row.season_year, r.row.meet_number or 0, r.row.meet_date or "")


def _fastest_pace(rows: list[ResultRow]) -> list[ResultRow]:
    paced = [r for r in rows if r.pace_seconds_per_mile]
    paced.sort(key=lambda r: r.pace_seconds_per_mile or 0.0)
    return paced[:LEADERBOARD_SIZE]


def _top_placements(rows: list[ResultRow]) -> list[ResultRow]:
    placed = [
        r
        for r in rows
        if r.row.place_overall is not None and r.row.place_overall <= LEADERBOARD_SIZE
    ]
    placed.sort(
        key=lambda r: (
            r.row.place_overall or 0,
            -r.row.season_year,
            -(r.row.meet_number or 0),
        )
    )
    return placed[:LEADERBOARD_SIZE]


def most_improved(
    rows: list[ResultRow], *, limit: int = LEADERBOARD_SIZE
) -> list[ImprovementEntry]:
    by_athlete: dict[str, list[ResultRow]] = defaultdict(list)
    for r in rows:
        if r.pace_seconds_per_mile:
            by_athlete[r.row.athlete_id].append(r)
    entries: list[ImprovementEntry] = []
    for athlete_rows in by_athlete.values():
        if len(athlete_rows) < 2:
            continue
        athlete_rows.sort(key=_chronological_key)
        first, latest = athlete_rows[0], athlete_rows[-1]
        first_pace = first.pace_seconds_per_mile or 0.0
        latest_pace = latest.pace_seconds_per_mile or 0.0
        improvement = first_pace - latest_pace
        entries.append(
            ImprovementEntry(
                athlete_id=latest.row.athlete_id,
                athlete_display_name=latest.row.athlete_display_name,
                school_display_name=latest.row.school_display_name,
                division_code=latest.row.division_code,
                races=len(athlete_rows),
                first_pace_seconds_per_mile=first_pace,
                latest_pace_seconds_per_mile=latest_pace,
                improvement_seconds_per_mile=improvement,
                improvement_pct=improvement / first_pace * 100.0,
            )
        )
    entries.sort(
        key=lambda e: (-e.improvement_seconds_per_mile, e.athlete_display_name)
    )
    return entries[:limit]


def get_overview(
    canonical: CanonicalReadRepository, filters: ResultFilters
) -> Overview:
    rows = list_results(canonical, filters)
    races_per_athlete: dict[str, int] = defaultdict(int)
    for r in rows:
        races_per_athlete[r.row.athlete_id] += 1
    metrics = OverviewMetrics(
        athletes=len(races_per_athlete),
        schools=len({r.row.school_id for r in rows}),
        meets=len({r.row.meet_id for r in rows}),
        seasons=len({r.row.season_year for r in rows}),
        results=len(rows),
        athletes_with_progress=sum(1 for n in races_per_athlete.values() if n > 1),
    )
    return Overview(
        metrics=metrics,
        fastest_pace=_fastest_pace(rows),
        top_placements=_top_placements(rows),
        most_improved=most_improved(rows),
    )


def get_athlete_profile(
    canonical: CanonicalReadRepository, athlete_id: str
) -> AthleteProfile | None:
    athlete = canonical.get_athlete(athlete_id)
    if athlete is None:
        return None
    rows = list_results(canonical, ResultFilters(athlete_id=athlete_id))
    rows.sort(key=_chronological_key)

    schools = {s.school_id: s.display_name for s in canonical.list_schools()}
    divisions: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        if r.row.division_code not in divisions[r.row.season_year]:
            divisions[r.row.season_year].append(r.row.division_code)
    seasons = [
        AthleteSeason(
            season_year=s.season_year,
            school_id=s.school_id,
            school_display_name=schools.get(s.school_id, s.school_id),
            grade=s.grade,
            gender_code=s.gender_code,
            division_codes=tuple(divisions.get(s.season_year, ())),
        )
        for s in canonical.list_athlete_seasons(athlete_id)
    ]

    times = [r.row.finish_time_ms for r in rows if r.row.finish_time_ms]
    paces = [r.pace_seconds_per_mile for r in rows if r.pace_seconds_per_mile]
    places = [r.row.place_overall for r in rows if r.row.place_overall]
    summary = AthleteSummaryStats(
        races=len(rows),
        seasons=len({r.row.season_year for r in rows}),
        best_time_ms=min(times) if times else None,
        best_pace_seconds_per_mile=min(paces) if paces else None,
        best_place=min(places) if places else None,
        latest_pace_seconds_per_mile=paces[-1] if paces else None,
    )
    # Race index, not season year: a within-season trend is the question a
    # profile page answers (legacy dashboard.py fitted over race order too).
    trend = compute_trend(
        [
            (float(i), r.pace_seconds_per_mile)
            for i, r in enumerate(rows)
            if r.pace_seconds_per_mile is not None
        ]
    )
    return AthleteProfile(
        athlete=athlete,
        seasons=seasons,
        summary=summary,
        results=rows,
        pace_trend=trend,
    )


def get_school_profile(
    canonical: CanonicalReadRepository,
    school_id: str,
    *,
    season_year: int | None = None,
) -> SchoolProfile | None:
    school = canonical.get_school(school_id)
    if school is None:
        return None
    all_rows = list_results(canonical, ResultFilters(school_id=school_id))
    per_season: dict[int, set[str]] = defaultdict(set)
    per_season_results: dict[int, int] = defaultdict(int)
    for r in all_rows:
        per_season[r.row.season_year].add(r.row.athlete_id)
        per_season_results[r.row.season_year] += 1
    seasons = [
        SchoolSeason(
            season_year=year,
            athletes=len(per_season[year]),
            results=per_season_results[year],
        )
        for year in sorted(per_season, reverse=True)
    ]

    scoped = [
        r for r in all_rows if season_year is None or r.row.season_year == season_year
    ]
    best_by_athlete: dict[str, ResultRow] = {}
    for r in scoped:
        if r.pace_seconds_per_mile is None:
            continue
        best = best_by_athlete.get(r.row.athlete_id)
        if best is None or r.pace_seconds_per_mile < (best.pace_seconds_per_mile or 0):
            best_by_athlete[r.row.athlete_id] = r
    top = sorted(
        best_by_athlete.values(), key=lambda r: r.pace_seconds_per_mile or 0.0
    )[:LEADERBOARD_SIZE]

    return SchoolProfile(
        school=school,
        seasons=seasons,
        team_scores=canonical.list_team_scores(
            season_year=season_year, school_id=school_id
        ),
        top_athletes=top,
    )
