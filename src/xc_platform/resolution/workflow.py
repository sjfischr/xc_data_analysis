"""Ties normalization, exact matching, and candidate scoring into the live
resolution workflow (Task 10.3), through resolution order step 4
(design.md section 10.1). Steps 5-6 (resolution agent, human review) are
the resolution agent (Task 11.4) and the existing
:class:`~xc_platform.db.repositories.resolution.ResolutionRepository`
review queue -- this module already opens review cases correctly for
them, it just doesn't attempt an agent call itself yet.

Default rollout policy (design.md section 10.5), implemented exactly here:

* deterministic source ID / approved alias / conflict-free canonical key:
  automatic (:func:`resolve_athlete`/:func:`resolve_school` return
  ``"auto_matched"``);
* no plausible (non-hard-blocked) candidate: automatic create, after
  validation (``"auto_created"``);
* any other candidate -- deterministic fuzzy or (once Task 11.4 exists)
  agent-proposed -- is review-only until the curated evaluation gate
  passes (``"review"``). Fuzzy scoring is treated exactly like an
  unevaluated agent proposal: never auto-applied by this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.migration.aliases import normalize_alias_value
from xc_platform.resolution.evidence import (
    AthleteResolutionInput,
    score_athlete_candidate,
    score_school_candidate,
)
from xc_platform.resolution.exact_match import (
    resolve_athlete_exact,
    resolve_school_exact,
)
from xc_platform.resolution.normalization import normalize_name_token

DISPOSITION_AUTO_MATCHED = "auto_matched"
DISPOSITION_AUTO_CREATED = "auto_created"
DISPOSITION_REVIEW = "review"


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    disposition: str
    entity_id: str | None
    resolution_case_id: str | None


def resolve_school(
    canonical: CanonicalReadRepository,
    writer: CanonicalWriteRepository,
    resolver: ResolutionRepository,
    *,
    source_id: str,
    ingest_run_id: str | None,
    raw_school_name: str,
) -> ResolutionOutcome:
    exact = resolve_school_exact(
        canonical, source_id=source_id, raw_school_name=raw_school_name
    )
    if exact is not None:
        return ResolutionOutcome(DISPOSITION_AUTO_MATCHED, exact, None)

    scored = [
        score_school_candidate(school, raw_school_name)
        for school in canonical.list_schools()
    ]
    plausible = [c for c in scored if c.score >= 0.5]

    # Task 19.3 policy change: a new school is only created automatically
    # while no schools exist at all (a first-ever load). Otherwise an
    # unmatched school name goes to review with every candidate listed --
    # the live 2026 Meet 1 intake silently created "BSM" (Basilica of St
    # Mary's abbreviation) and "St. Francis of Assisi (Triangle)" as new
    # schools, splitting 44 results off their real teams. The league has
    # ~30 parishes and new ones are rare; a click is cheaper than a split.
    if not plausible and not scored:
        school_id = writer.create_school(canonical_name=raw_school_name)
        writer.add_school_alias(
            school_id=school_id,
            source_id=source_id,
            raw_value=raw_school_name,
            normalized_value=normalize_alias_value(raw_school_name),
        )
        return ResolutionOutcome(DISPOSITION_AUTO_CREATED, school_id, None)

    existing = resolver.find_pending_case(
        ingest_run_id=ingest_run_id,
        entity_type="school",
        evidence_match={"raw_school_name": raw_school_name},
    )
    if existing is not None:
        return ResolutionOutcome(DISPOSITION_REVIEW, None, existing.resolution_case_id)

    best = max(scored, key=lambda c: c.score)
    case_id = resolver.open_case(
        entity_type="school",
        ingest_run_id=ingest_run_id,
        candidate_entity_id=best.entity_id if plausible else None,
        confidence=best.score,
        evidence_json=json.dumps(
            {
                "raw_school_name": raw_school_name,
                "source_id": source_id,
                "reason": None if plausible else "no close match -- new school?",
                "candidates": [
                    {
                        "school_id": c.entity_id,
                        "score": c.score,
                        "evidence_codes": list(c.evidence_codes),
                    }
                    for c in scored
                    if c.score > 0
                ],
            }
        ),
    )
    return ResolutionOutcome(DISPOSITION_REVIEW, None, case_id)


def resolve_athlete(
    canonical: CanonicalReadRepository,
    writer: CanonicalWriteRepository,
    resolver: ResolutionRepository,
    *,
    source_id: str,
    ingest_run_id: str | None,
    incoming: AthleteResolutionInput,
    raw_first_name: str,
    raw_last_name: str,
    context_key: str = "",
) -> ResolutionOutcome:
    exact = resolve_athlete_exact(
        canonical,
        source_id=source_id,
        raw_first_name=raw_first_name,
        raw_last_name=raw_last_name,
        context_key=context_key,
    )
    if exact is not None:
        return ResolutionOutcome(DISPOSITION_AUTO_MATCHED, exact, None)

    normalized_last = normalize_name_token(raw_last_name)
    pool = canonical.find_athletes_by_normalized_last_name(normalized_last)

    raw_full_name = f"{raw_first_name} {raw_last_name}"
    normalized_alias = normalize_alias_value(raw_full_name)
    scored = [
        score_athlete_candidate(
            candidate,
            incoming,
            seasons=canonical.list_athlete_seasons(candidate.athlete_id),
            has_result_in_race=canonical.athlete_has_result_in_race(
                athlete_id=candidate.athlete_id, race_id=incoming.race_id
            ),
            has_bib_match=(
                incoming.bib is not None
                and canonical.athlete_has_bib_in_season(
                    athlete_id=candidate.athlete_id,
                    season_year=incoming.season_year,
                    bib=incoming.bib,
                )
            ),
            confirmed_distinct=canonical.athlete_confirmed_distinct(
                candidate_athlete_id=candidate.athlete_id, raw_full_name=raw_full_name
            ),
            source_id_bound_elsewhere=any(
                bound_id != candidate.athlete_id
                for bound_id in canonical.athlete_ids_bound_to_source_identity(
                    source_id=source_id,
                    normalized_value=normalized_alias,
                    context_key=context_key,
                )
            ),
        )
        for candidate in pool
    ]
    viable = [c for c in scored if not c.is_hard_blocked and c.score > 0]

    if not viable:
        athlete_id = writer.create_athlete(display_name=raw_full_name)
        writer.add_athlete_alias(
            athlete_id=athlete_id,
            source_id=source_id,
            raw_value=raw_full_name,
            normalized_value=normalized_alias,
            context_key=context_key,
        )
        if incoming.school_id is not None and incoming.gender_code is not None:
            writer.upsert_athlete_season(
                athlete_id=athlete_id,
                season_year=incoming.season_year,
                school_id=incoming.school_id,
                grade=incoming.grade,
                gender_code=incoming.gender_code,
            )
        return ResolutionOutcome(DISPOSITION_AUTO_CREATED, athlete_id, None)

    existing = resolver.find_pending_case(
        ingest_run_id=ingest_run_id,
        entity_type="athlete",
        evidence_match={
            "raw_first_name": raw_first_name,
            "raw_last_name": raw_last_name,
            "context_key": context_key,
        },
    )
    if existing is not None:
        return ResolutionOutcome(DISPOSITION_REVIEW, None, existing.resolution_case_id)

    best = max(viable, key=lambda c: c.score)
    case_id = resolver.open_case(
        entity_type="athlete",
        ingest_run_id=ingest_run_id,
        candidate_entity_id=best.entity_id,
        confidence=best.score,
        evidence_json=json.dumps(
            {
                "raw_first_name": raw_first_name,
                "raw_last_name": raw_last_name,
                # Task 19.3: what a "match to existing" decision needs to
                # write the exact alias resolve_athlete_exact looks up.
                "source_id": source_id,
                "context_key": context_key,
                "season_year": incoming.season_year,
                "grade": incoming.grade,
                "raw_school_id": incoming.school_id,
                "candidates": [
                    {
                        "athlete_id": c.entity_id,
                        "score": c.score,
                        "evidence_codes": list(c.evidence_codes),
                        "conflict_codes": list(c.conflict_codes),
                    }
                    for c in scored
                ],
            }
        ),
    )
    return ResolutionOutcome(DISPOSITION_REVIEW, None, case_id)
