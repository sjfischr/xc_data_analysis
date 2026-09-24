"""Dashboard aggregate tests (Task 19.1)."""

from __future__ import annotations

from xc_platform.analytics.dashboard import enrich, most_improved
from xc_platform.db.repositories.canonical import ResultRowRecord


def _row(
    athlete_id: str,
    *,
    season_year: int,
    meet_number: int,
    finish_time_ms: int,
    distance_meters: int = 3000,
) -> ResultRowRecord:
    return ResultRowRecord(
        result_id=f"{athlete_id}-{season_year}-{meet_number}",
        race_id="race",
        meet_id=f"meet-{season_year}-{meet_number}",
        athlete_id=athlete_id,
        athlete_display_name=athlete_id.title(),
        school_id="s1",
        school_display_name="St Agnes",
        season_year=season_year,
        meet_number=meet_number,
        meet_name="Meet",
        meet_date=None,
        division_code="JV",
        gender_code="F",
        distance_meters=distance_meters,
        finish_time_ms=finish_time_ms,
        place_overall=1,
        grade=6,
    )


def test_enrich_computes_pace_and_speed_from_distance() -> None:
    r = enrich(_row("a", season_year=2025, meet_number=1, finish_time_ms=900_000))
    # 900 s over 3000 m (1.864 mi): 482.8 s/mi, 7.46 mph.
    assert r.pace_seconds_per_mile is not None
    assert abs(r.pace_seconds_per_mile - 482.8) < 0.1
    assert r.speed_mph is not None
    assert abs(r.speed_mph - 7.456) < 0.01


def test_enrich_leaves_pace_empty_without_a_distance() -> None:
    r = enrich(
        _row("a", season_year=2025, meet_number=1, finish_time_ms=1, distance_meters=0)
    )
    assert r.pace_seconds_per_mile is None
    assert r.speed_mph is None


def test_most_improved_orders_chronologically_across_seasons() -> None:
    """Legacy dashboard.py sorted by meet number alone, which put a 2025
    Meet 1 before a 2024 Meet 3 in all-seasons mode."""
    rows = [
        enrich(_row("a", season_year=2025, meet_number=1, finish_time_ms=800_000)),
        enrich(_row("a", season_year=2024, meet_number=3, finish_time_ms=1_000_000)),
    ]
    [entry] = most_improved(rows)
    assert entry.first_pace_seconds_per_mile > entry.latest_pace_seconds_per_mile
    assert entry.improvement_seconds_per_mile > 0
    assert entry.races == 2


def test_most_improved_skips_single_race_athletes_and_ranks_by_gain() -> None:
    rows = [
        enrich(_row("solo", season_year=2025, meet_number=1, finish_time_ms=700_000)),
        enrich(_row("small", season_year=2025, meet_number=1, finish_time_ms=900_000)),
        enrich(_row("small", season_year=2025, meet_number=2, finish_time_ms=890_000)),
        enrich(_row("big", season_year=2025, meet_number=1, finish_time_ms=900_000)),
        enrich(_row("big", season_year=2025, meet_number=2, finish_time_ms=800_000)),
    ]
    assert [e.athlete_id for e in most_improved(rows)] == ["big", "small"]
