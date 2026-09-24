"""Versioned request/response contracts (Task 13.1, design.md section 13:
"All APIs are versioned under `/api/v1`. Responses include `request_id`
and relevant `publication_id`.").
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Envelope(BaseModel):
    """Every response body is wrapped in this so ``request_id`` (and, for
    data responses, ``publication_id``) is always present and always in
    the same place -- a client never has to special-case which endpoint it
    called to find them (Requirement 10.5/18.1)."""

    request_id: str
    publication_id: str | None = None
    data: Any = None


class ErrorDetail(BaseModel):
    """A typed, user-safe error (Requirement 18.4): a stable ``code`` a
    client can branch on, a ``message`` safe to show a user, and never a
    stack trace or internal detail."""

    code: str
    message: str


class ErrorEnvelope(BaseModel):
    request_id: str
    error: ErrorDetail


class DimensionsResponse(BaseModel):
    season_years: list[int]
    divisions: list[str]
    gender_codes: list[str]
    school_count: int
    athlete_count: int
    meet_numbers: list[int] = Field(default_factory=list)
    grades: list[int] = Field(default_factory=list)


class AthleteSummary(BaseModel):
    athlete_id: str
    display_name: str


class SchoolSummary(BaseModel):
    school_id: str
    display_name: str


class TeamScoreEntry(BaseModel):
    season_year: int
    meet_number: int
    division_code: str
    gender_code: str
    school_id: str
    school_display_name: str
    score: int
    scoring_runners: int
    avg_time_s: float | None
    team_rank: int


class SaintSebastianStandingEntry(BaseModel):
    season_year: int
    division_code: str
    gender_code: str
    athlete_id: str
    athlete_display_name: str
    school_id: str
    school_display_name: str
    cumulative_time_ms: int
    meets_run: int
    standing_rank: int
    time_back_ms: int


class SchoolRosterEntry(BaseModel):
    athlete_id: str
    athlete_display_name: str
    season_year: int
    grade: int | None
    gender_code: str


class ProgressionEntry(BaseModel):
    season_year: int
    meet_name: str
    division_code: str
    distance_meters: int | None
    finish_time_ms: int | None
    place_overall: int | None
    pace_seconds_per_mile: float | None


class TrendSummary(BaseModel):
    implementation_version: str
    sample_size: int
    has_confidence: bool
    observed_change: float
    slope_per_x: float | None
    r_squared: float | None
    span: float | None


class AthleteProgressionResponse(BaseModel):
    athlete_id: str
    entries: list[ProgressionEntry]
    pace_trend: TrendSummary | None


class ResultRowEntry(BaseModel):
    """One result with athlete/school/meet context (Task 19.1)."""

    result_id: str
    race_id: str
    meet_id: str
    athlete_id: str
    athlete_display_name: str
    school_id: str
    school_display_name: str
    season_year: int
    meet_number: int | None
    meet_name: str
    meet_date: str | None
    division_code: str
    gender_code: str
    distance_meters: int | None
    finish_time_ms: int | None
    place_overall: int | None
    grade: int | None
    pace_seconds_per_mile: float | None
    speed_mph: float | None


class OverviewMetricsEntry(BaseModel):
    athletes: int
    schools: int
    meets: int
    seasons: int
    results: int
    athletes_with_progress: int


class ImprovementEntry(BaseModel):
    athlete_id: str
    athlete_display_name: str
    school_display_name: str
    division_code: str
    races: int
    first_pace_seconds_per_mile: float
    latest_pace_seconds_per_mile: float
    improvement_seconds_per_mile: float
    improvement_pct: float


class OverviewResponse(BaseModel):
    metrics: OverviewMetricsEntry
    fastest_pace: list[ResultRowEntry]
    top_placements: list[ResultRowEntry]
    most_improved: list[ImprovementEntry]


class AthleteSeasonEntry(BaseModel):
    season_year: int
    school_id: str
    school_display_name: str
    grade: int | None
    gender_code: str
    division_codes: list[str]


class AthleteSummaryStatsEntry(BaseModel):
    races: int
    seasons: int
    best_time_ms: int | None
    best_pace_seconds_per_mile: float | None
    best_place: int | None
    latest_pace_seconds_per_mile: float | None


class AthleteProfileResponse(BaseModel):
    athlete_id: str
    display_name: str
    seasons: list[AthleteSeasonEntry]
    summary: AthleteSummaryStatsEntry
    results: list[ResultRowEntry]
    pace_trend: TrendSummary | None


class SchoolSeasonEntry(BaseModel):
    season_year: int
    athletes: int
    results: int


class SchoolProfileResponse(BaseModel):
    school_id: str
    display_name: str
    seasons: list[SchoolSeasonEntry]
    team_scores: list[TeamScoreEntry]
    top_athletes: list[ResultRowEntry]


class IngestSubmitRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class IngestRunResponse(BaseModel):
    ingest_run_id: str
    state: str
    inserted_count: int
    quarantined_count: int
    error_summary: str | None


class CommitResponse(BaseModel):
    ingest_run_id: str
    publication_id: str
    inserted_count: int
    already_committed: bool
