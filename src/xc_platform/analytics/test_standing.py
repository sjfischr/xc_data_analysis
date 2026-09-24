"""Standing-in-the-field and projection model (2026-09-24)."""

from __future__ import annotations

import random
import sqlite3
import statistics

from xc_platform.analytics.standing import (
    build_field,
    fit,
    percentile_in,
    project_standing,
    training_pairs,
)
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def test_percentile_is_the_share_of_the_field_beaten() -> None:
    field = [0.8, 0.9, 1.0, 1.1, 1.2]
    assert percentile_in(field, 0.8) == 100.0
    assert percentile_in(field, 1.0) == 50.0
    assert percentile_in(field, 1.2) == 0.0


def _seed(conn: sqlite3.Connection, *, seasons: list[int], runners: int = 60) -> None:
    """Every season: a field whose times swing with course conditions
    (Meet 3 is 30% faster for everyone), while each runner's standing
    regresses toward the median with noise."""
    write = CanonicalWriteRepository(conn)
    historical = HistoricalCanonicalWriter(conn)
    source = StagingRepository(conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    run = IngestRunRepository(conn).create_run(
        source_id=source,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="c",
    )
    school = write.create_school(canonical_name="St Agnes")
    rng = random.Random(7)  # noqa: S311 -- test data
    for season in seasons:
        races = {}
        for meet, speed in ((1, 1.0), (3, 0.7)):
            meet_id = historical.get_or_create_meet(
                season_year=season, meet_number=meet, name=None, series="NVJCYO"
            )
            races[meet] = (
                historical.get_or_create_race(
                    meet_id=meet_id,
                    division_code="JV",
                    gender_code="M",
                    distance_meters=3000,
                ),
                speed,
            )
        for i in range(runners):
            athlete = write.create_athlete(display_name=f"Runner {season} {i}")
            start = 0.7 + 0.6 * i / runners
            later = 1 + 0.8 * (start - 1) + rng.gauss(0, 0.03)
            for meet, ratio in ((1, start), (3, later)):
                race_id, speed = races[meet]
                historical.insert_result(
                    source_id=source,
                    race_id=race_id,
                    athlete_id=athlete,
                    school_id=school,
                    ingest_run_id=run,
                    finish_time_ms=int(900_000 * speed * ratio),
                    original_time_text=None,
                    place_overall=None,
                    bib=None,
                    grade=None,
                    scored_flag="scored",
                )


def test_standing_cancels_a_field_wide_course_swing(conn: sqlite3.Connection) -> None:
    _seed(conn, seasons=[2024, 2025])
    fld = build_field(CanonicalReadRepository(conn).list_result_rows())
    pairs = training_pairs(fld, from_meet=1, to_meet=3, seasons={2024, 2025})
    model = fit(pairs, (2024, 2025))
    assert model is not None
    # Every time dropped 30% (the course), yet standing barely moves: the
    # typical change in standing is a few points, not a 30% swing.
    changes = [later - start for start, later in model.pairs]
    assert abs(statistics.median(changes)) < 5


def test_projection_gives_ranges_probabilities_and_never_leaves_history(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn, seasons=[2023, 2024, 2025])
    canonical = CanonicalReadRepository(conn)
    leader = canonical.search_athletes_by_name("Runner 2025 0")[0].athlete_id
    back = canonical.search_athletes_by_name("Runner 2025 59")[0].athlete_id

    result = project_standing(canonical, [leader, back], season_year=2025)
    by_id = {p.athlete_id: p for p in result.athletes}

    assert result.model is not None
    for projection in result.athletes:
        low, high = projection.percentile_range_80
        # Bounded by construction: no projection can leave 0-100.
        assert 0.0 <= low <= projection.expected_percentile <= high <= 100.0
        best, worst = projection.place_range_80
        assert 1 <= best <= projection.expected_place <= worst
        assert 0.0 <= projection.probability_holds_or_improves <= 1.0
    assert by_id[leader].expected_place < by_id[back].expected_place
    [pair] = result.head_to_head
    assert float(str(pair["probability_a_finishes_ahead"])) > 0.9
    assert {b.test_season for b in result.backtest} <= {2023, 2024}
    for season in result.backtest:
        assert 0.0 <= season.range_80_held_share <= 1.0


def test_projection_explains_when_there_is_nothing_to_project_from(
    conn: sqlite3.Connection,
) -> None:
    _seed(conn, seasons=[2023, 2024])
    canonical = CanonicalReadRepository(conn)
    runner = canonical.search_athletes_by_name("Runner 2024 3")[0].athlete_id
    result = project_standing(canonical, [runner, "missing"], season_year=2026)
    assert result.athletes == []
    assert "no 2026 result" in result.unavailable[runner]
    assert result.unavailable["missing"] == "no athlete with that id"
