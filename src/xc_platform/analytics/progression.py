"""Finish-time/place history and pace progression (Task 11.1, design.md
11.1's approved "finish-time and place history" and "pace per mile/km"
calculations, applying 11.2's statistical safeguards via
:mod:`xc_platform.analytics.trend`).
"""

from __future__ import annotations

from dataclasses import dataclass

from xc_platform.analytics.trend import TrendResult, compute_trend
from xc_platform.db.repositories.canonical import (
    CanonicalReadRepository,
    ResultHistoryRecord,
)

METERS_PER_MILE = 1609.344


@dataclass(frozen=True, slots=True)
class ProgressionEntry:
    result: ResultHistoryRecord
    pace_seconds_per_mile: float | None


@dataclass(frozen=True, slots=True)
class AthleteProgression:
    athlete_id: str
    entries: list[ProgressionEntry]
    pace_trend: TrendResult | None


def _pace_seconds_per_mile(result: ResultHistoryRecord) -> float | None:
    if result.finish_time_ms is None or not result.distance_meters:
        return None
    seconds = result.finish_time_ms / 1000.0
    miles = result.distance_meters / METERS_PER_MILE
    if miles <= 0:
        return None
    return seconds / miles


def get_athlete_progression(
    canonical: CanonicalReadRepository, athlete_id: str
) -> AthleteProgression:
    """Distance is normalized to pace/mile so results across different race
    distances are comparable (design.md 11.1/11.2's "cross-distance
    comparisons use pace"). A result with no recorded distance or finish
    time contributes to the history but not the pace trend -- it is
    neither dropped nor silently treated as zero."""
    history = canonical.list_results_for_athlete(athlete_id)
    entries = [
        ProgressionEntry(result=r, pace_seconds_per_mile=_pace_seconds_per_mile(r))
        for r in history
    ]
    trend_points: list[tuple[float, float]] = [
        (float(e.result.season_year), e.pace_seconds_per_mile)
        for e in entries
        if e.pace_seconds_per_mile is not None
    ]
    trend = compute_trend(trend_points)
    return AthleteProgression(athlete_id=athlete_id, entries=entries, pace_trend=trend)
