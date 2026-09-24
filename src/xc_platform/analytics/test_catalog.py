from __future__ import annotations

import sqlite3

from xc_platform.analytics.catalog import find_athletes, find_schools, list_dimensions
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def test_list_dimensions_reflects_seeded_data(conn: sqlite3.Connection) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    historical = HistoricalCanonicalWriter(conn)

    write.create_school(canonical_name="St Agnes")
    write.create_athlete(display_name="Jane Doe")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name="Meet 1", series="NVJCYO"
    )
    historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )

    summary = list_dimensions(canonical)
    assert summary.season_years == [2026]
    assert summary.divisions == ["Varsity"]
    assert summary.school_count == 1
    assert summary.athlete_count == 1


def test_find_athletes_is_a_case_insensitive_substring_search(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    write.create_athlete(display_name="Gwendolyn Fischer")
    write.create_athlete(display_name="Lucy DeMarr")

    results = find_athletes(canonical, "fischer")
    assert [a.display_name for a in results] == ["Gwendolyn Fischer"]


def test_find_schools_is_a_case_insensitive_substring_search(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    canonical = CanonicalReadRepository(conn)
    write.create_school(canonical_name="St Agnes")
    write.create_school(canonical_name="Holy Family")

    results = find_schools(canonical, "agnes")
    assert [s.display_name for s in results] == ["St Agnes"]
