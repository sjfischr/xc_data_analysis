"""Team scoring and Saint Sebastian standings (Task 7.3/11.1, design.md
11.1). The scoring rules themselves live in ``v_team_scores``/
``v_saint_sebastian`` (migrations/0003_analytics_views.sql), already
parity-tested against the frozen historical baseline with zero
discrepancies (migration/parity.py) -- these are thin pass-throughs, not
a reimplementation.

Plain, runtime-neutral functions -- no Strands import here, same as
:mod:`xc_platform.analytics.catalog`.
"""

from __future__ import annotations

from xc_platform.db.repositories.canonical import (
    CanonicalReadRepository,
    SaintSebastianStandingRecord,
    TeamScoreRecord,
)


def get_team_scores(
    canonical: CanonicalReadRepository,
    *,
    season_year: int | None = None,
    meet_number: int | None = None,
    division_code: str | None = None,
    gender_code: str | None = None,
    school_id: str | None = None,
) -> list[TeamScoreRecord]:
    return canonical.list_team_scores(
        season_year=season_year,
        meet_number=meet_number,
        division_code=division_code,
        gender_code=gender_code,
        school_id=school_id,
    )


def get_saint_sebastian_standings(
    canonical: CanonicalReadRepository,
    *,
    season_year: int | None = None,
    division_code: str | None = None,
    gender_code: str | None = None,
) -> list[SaintSebastianStandingRecord]:
    return canonical.list_saint_sebastian_standings(
        season_year=season_year,
        division_code=division_code,
        gender_code=gender_code,
    )
