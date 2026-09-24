"""Read/analytics endpoints (Task 13.2, design.md section 13's Read APIs).

Every route here pins exactly one publication for the whole request
(Requirement 3.6/9): :func:`_pinned_connection` resolves
``ctx.snapshot_reader.current()`` once, opens it read-only, and the
response envelope always carries that ``publication_id`` -- a client can
always tell which data version an answer came from.

Team scores and Saint Sebastian standings are real here, wrapping the
parity-tested ``v_team_scores``/``v_saint_sebastian`` views (migrations/
0003_analytics_views.sql) via :mod:`xc_platform.analytics.team_scores`.
Improvement candidates, head-to-head comparisons, and what-if scenarios
are still NOT implemented -- design.md 11.1 lists them as approved
calculations, but no business rule for any of them exists anywhere yet
(documented gap, tasks.md Task 11.1); there is no placeholder route for
those three.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from xc_platform.analytics.catalog import find_athletes, find_schools, list_dimensions
from xc_platform.analytics.dashboard import (
    ResultFilters,
    ResultRow,
    get_athlete_profile,
    get_overview,
    get_school_profile,
    list_results,
)
from xc_platform.analytics.progression import get_athlete_progression
from xc_platform.analytics.team_scores import (
    get_saint_sebastian_standings,
    get_team_scores,
)
from xc_platform.api.context import AppContext
from xc_platform.api.deps import get_context, require_session
from xc_platform.api.schemas import (
    AthleteProfileResponse,
    AthleteProgressionResponse,
    AthleteSeasonEntry,
    AthleteSummary,
    AthleteSummaryStatsEntry,
    DimensionsResponse,
    Envelope,
    ImprovementEntry,
    OverviewMetricsEntry,
    OverviewResponse,
    ProgressionEntry,
    ResultRowEntry,
    SaintSebastianStandingEntry,
    SchoolProfileResponse,
    SchoolRosterEntry,
    SchoolSeasonEntry,
    SchoolSummary,
    TeamScoreEntry,
    TrendSummary,
)
from xc_platform.db.publication.reader import PinnedSnapshot
from xc_platform.db.repositories.canonical import CanonicalReadRepository

router = APIRouter(
    prefix="/api/v1", tags=["catalog"], dependencies=[Depends(require_session)]
)


@contextmanager
def _pinned(
    ctx: AppContext,
) -> Iterator[tuple[CanonicalReadRepository, PinnedSnapshot]]:
    pinned = ctx.snapshot_reader.current()
    connection = pinned.open_connection()
    try:
        yield CanonicalReadRepository(connection), pinned
    finally:
        connection.close()


@router.get("/catalog/filters", response_model=Envelope)
def catalog_filters(
    request: Request, ctx: AppContext = Depends(get_context)
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        summary = list_dimensions(canonical)
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=DimensionsResponse(**dataclasses.asdict(summary)).model_dump(),
    )


@router.get("/athletes", response_model=Envelope)
def athletes(
    request: Request,
    q: str = "",
    limit: int = Query(default=20, ge=1, le=200),
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        matches = find_athletes(canonical, q, limit=limit)
        data = [
            AthleteSummary(
                athlete_id=a.athlete_id, display_name=a.display_name
            ).model_dump()
            for a in matches
        ]
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=data,
    )


@router.get("/schools", response_model=Envelope)
def schools(
    request: Request,
    q: str = "",
    limit: int = Query(default=20, ge=1, le=200),
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        matches = find_schools(canonical, q, limit=limit)
        data = [
            SchoolSummary(
                school_id=s.school_id, display_name=s.display_name
            ).model_dump()
            for s in matches
        ]
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=data,
    )


@router.get("/athletes/{athlete_id}/progression", response_model=Envelope)
def athlete_progression(
    request: Request, athlete_id: str, ctx: AppContext = Depends(get_context)
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        if canonical.get_athlete(athlete_id) is None:
            raise HTTPException(status_code=404, detail="athlete not found")
        progression = get_athlete_progression(canonical, athlete_id)
        trend = progression.pace_trend
        payload = AthleteProgressionResponse(
            athlete_id=progression.athlete_id,
            entries=[
                ProgressionEntry(
                    season_year=e.result.season_year,
                    meet_name=e.result.meet_name,
                    division_code=e.result.division_code,
                    distance_meters=e.result.distance_meters,
                    finish_time_ms=e.result.finish_time_ms,
                    place_overall=e.result.place_overall,
                    pace_seconds_per_mile=e.pace_seconds_per_mile,
                )
                for e in progression.entries
            ],
            pace_trend=TrendSummary(**dataclasses.asdict(trend)) if trend else None,
        )
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=payload.model_dump(),
    )


@router.get("/team-scores", response_model=Envelope)
def team_scores(
    request: Request,
    season_year: int | None = None,
    meet_number: int | None = None,
    division_code: str | None = None,
    gender_code: str | None = None,
    school_id: str | None = None,
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        entries = get_team_scores(
            canonical,
            season_year=season_year,
            meet_number=meet_number,
            division_code=division_code,
            gender_code=gender_code,
            school_id=school_id,
        )
        data = [TeamScoreEntry(**dataclasses.asdict(e)).model_dump() for e in entries]
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=data,
    )


@router.get("/saint-sebastian-standings", response_model=Envelope)
def saint_sebastian_standings(
    request: Request,
    season_year: int | None = None,
    division_code: str | None = None,
    gender_code: str | None = None,
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        entries = get_saint_sebastian_standings(
            canonical,
            season_year=season_year,
            division_code=division_code,
            gender_code=gender_code,
        )
        data = [
            SaintSebastianStandingEntry(**dataclasses.asdict(e)).model_dump()
            for e in entries
        ]
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=data,
    )


@router.get("/schools/{school_id}/roster", response_model=Envelope)
def school_roster(
    request: Request,
    school_id: str,
    season_year: int | None = None,
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        if canonical.get_school(school_id) is None:
            raise HTTPException(status_code=404, detail="school not found")
        entries = canonical.list_athletes_for_school(school_id, season_year=season_year)
        data = [
            SchoolRosterEntry(**dataclasses.asdict(e)).model_dump() for e in entries
        ]
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=data,
    )


MAX_RESULT_ROWS = 5000


def _result_row(r: ResultRow) -> ResultRowEntry:
    return ResultRowEntry(
        **dataclasses.asdict(r.row),
        pace_seconds_per_mile=r.pace_seconds_per_mile,
        speed_mph=r.speed_mph,
        percentile=r.percentile,
    )


def _filters(
    season_year: int | None = None,
    school_id: str | None = None,
    athlete_id: str | None = None,
    division_code: str | None = None,
    gender_code: str | None = None,
    meet_number: list[int] = Query(default=[]),
    grade: list[int] = Query(default=[]),
    race_id: str | None = None,
) -> ResultFilters:
    return ResultFilters(
        season_year=season_year,
        school_id=school_id,
        athlete_id=athlete_id,
        division_code=division_code,
        gender_code=gender_code,
        meet_numbers=tuple(meet_number),
        grades=tuple(grade),
        race_id=race_id,
    )


@router.get("/results", response_model=Envelope)
def results(
    request: Request,
    filters: ResultFilters = Depends(_filters),
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    """Flat, filterable results (Task 19.1) -- meet result tables and the
    agent's chart data both read this shape."""
    with _pinned(ctx) as (canonical, pinned):
        rows = list_results(canonical, filters)[:MAX_RESULT_ROWS]
        data = [_result_row(r).model_dump() for r in rows]
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=data,
    )


@router.get("/overview", response_model=Envelope)
def overview(
    request: Request,
    filters: ResultFilters = Depends(_filters),
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        result = get_overview(canonical, filters)
        payload = OverviewResponse(
            metrics=OverviewMetricsEntry(**dataclasses.asdict(result.metrics)),
            fastest_pace=[_result_row(r) for r in result.fastest_pace],
            top_placements=[_result_row(r) for r in result.top_placements],
            most_improved=[
                ImprovementEntry(**dataclasses.asdict(e)) for e in result.most_improved
            ],
        )
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=payload.model_dump(),
    )


@router.get("/athletes/{athlete_id}/profile", response_model=Envelope)
def athlete_profile(
    request: Request, athlete_id: str, ctx: AppContext = Depends(get_context)
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        profile = get_athlete_profile(canonical, athlete_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="athlete not found")
        trend = profile.pace_trend
        payload = AthleteProfileResponse(
            athlete_id=profile.athlete.athlete_id,
            display_name=profile.athlete.display_name,
            seasons=[
                AthleteSeasonEntry(
                    **{
                        **dataclasses.asdict(s),
                        "division_codes": list(s.division_codes),
                    }
                )
                for s in profile.seasons
            ],
            summary=AthleteSummaryStatsEntry(**dataclasses.asdict(profile.summary)),
            results=[_result_row(r) for r in profile.results],
            pace_trend=TrendSummary(**dataclasses.asdict(trend)) if trend else None,
        )
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=payload.model_dump(),
    )


@router.get("/schools/{school_id}/profile", response_model=Envelope)
def school_profile(
    request: Request,
    school_id: str,
    season_year: int | None = None,
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    with _pinned(ctx) as (canonical, pinned):
        profile = get_school_profile(canonical, school_id, season_year=season_year)
        if profile is None:
            raise HTTPException(status_code=404, detail="school not found")
        payload = SchoolProfileResponse(
            school_id=profile.school.school_id,
            display_name=profile.school.display_name,
            seasons=[
                SchoolSeasonEntry(**dataclasses.asdict(s)) for s in profile.seasons
            ],
            team_scores=[
                TeamScoreEntry(**dataclasses.asdict(t)) for t in profile.team_scores
            ],
            top_athletes=[_result_row(r) for r in profile.top_athletes],
        )
    return Envelope(
        request_id=request.state.request_id,
        publication_id=pinned.manifest.publication_id,
        data=payload.model_dump(),
    )
