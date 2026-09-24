"""Resolution order steps 2-3: approved aliases and conflict-free
normalized canonical keys (design.md section 10.1, Task 10.1).

Step 1 (stable upstream source entity link) is meet/race-scoped and
already implemented as
:meth:`~xc_platform.db.repositories.canonical.CanonicalReadRepository.get_source_entity_link`
(Task 4.4) -- nothing to add here for it. Steps 4-6 (candidate scoring,
resolution agent, human review) are
:mod:`xc_platform.resolution.evidence`/:mod:`xc_platform.resolution.workflow`
and the resolution agent (Task 11.4).

Each function here only ever returns an id it is fully confident is
correct, or ``None`` to signal "fall through to the next step" -- including
when a normalized key matches more than one canonical entity, which is
treated as a conflict, not resolved by picking either one (design.md
10.1's "conflict-free" qualifier on step 3).
"""

from __future__ import annotations

from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.migration.aliases import normalize_alias_value
from xc_platform.resolution.normalization import (
    normalize_name_token,
    normalize_school_key,
)


def resolve_school_exact(
    canonical: CanonicalReadRepository,
    *,
    source_id: str,
    raw_school_name: str,
    context_key: str = "",
) -> str | None:
    alias_match = canonical.resolve_school_alias(
        source_id=source_id,
        normalized_value=normalize_alias_value(raw_school_name),
        context_key=context_key,
    )
    if alias_match is not None:
        return alias_match

    target_key = normalize_school_key(raw_school_name)
    matches = [
        school
        for school in canonical.list_schools()
        if normalize_school_key(school.canonical_name) == target_key
    ]
    if len(matches) == 1:
        return matches[0].school_id
    return None


def resolve_athlete_exact(
    canonical: CanonicalReadRepository,
    *,
    source_id: str,
    raw_first_name: str,
    raw_last_name: str,
    context_key: str = "",
) -> str | None:
    alias_match = canonical.resolve_athlete_alias(
        source_id=source_id,
        normalized_value=normalize_alias_value(f"{raw_first_name} {raw_last_name}"),
        context_key=context_key,
    )
    if alias_match is not None:
        return alias_match

    target_first = normalize_name_token(raw_first_name)
    target_last = normalize_name_token(raw_last_name)
    pool = canonical.find_athletes_by_normalized_last_name(target_last)
    matches = [
        athlete
        for athlete in pool
        if normalize_name_token(athlete.canonical_first_name) == target_first
    ]
    if len(matches) == 1:
        return matches[0].athlete_id
    return None
