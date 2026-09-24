"""Schema-discovery and entity-lookup tool logic (Task 11.1, design.md
11.3's ``list_dimensions``/``find_athletes``/``find_schools``).

Plain, runtime-neutral functions -- no Strands import here. The thin
``@tool``-decorated adapters that bind these to a pinned snapshot connection
live in :mod:`xc_platform.agents.tools`, so this module (and its tests) has
no dependency on the agent framework at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from xc_platform.db.repositories.canonical import (
    AthleteRecord,
    CanonicalReadRepository,
    SchoolRecord,
)


@dataclass(frozen=True, slots=True)
class DimensionsSummary:
    season_years: list[int]
    divisions: list[str]
    gender_codes: list[str]
    school_count: int
    athlete_count: int
    meet_numbers: list[int]
    grades: list[int]


def list_dimensions(canonical: CanonicalReadRepository) -> DimensionsSummary:
    return DimensionsSummary(
        season_years=canonical.list_season_years(),
        divisions=canonical.list_divisions(),
        gender_codes=["F", "M", "X"],
        school_count=canonical.count_schools(),
        athlete_count=canonical.count_athletes(),
        meet_numbers=canonical.list_meet_numbers(),
        grades=canonical.list_grades(),
    )


def find_athletes(
    canonical: CanonicalReadRepository, query: str, *, limit: int = 20
) -> list[AthleteRecord]:
    return canonical.search_athletes_by_name(query, limit=limit)


def find_schools(
    canonical: CanonicalReadRepository, query: str, *, limit: int = 20
) -> list[SchoolRecord]:
    return canonical.search_schools_by_name(query, limit=limit)
