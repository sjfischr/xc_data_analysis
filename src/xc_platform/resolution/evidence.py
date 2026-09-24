"""Deterministic candidate evidence scoring and hard-conflict detection for
athlete resolution (Task 10.2, design.md sections 10.3-10.4).

Meet resolution already has a fully deterministic identity path: a source
race/event/result-set ID either has a
:meth:`~xc_platform.db.repositories.canonical.CanonicalReadRepository.get_source_entity_link`
row (resolution order step 1) or it doesn't, in which case it is a new
meet/race, not a fuzzy-matching problem. So there is no meet
candidate-scoring path here; Requirement 8.3's "bounded meet ... candidates"
is satisfied by that exact lookup plus human review (Task 12.1), not fuzzy
meet matching.

School candidate scoring is deliberately simpler than athlete scoring: the
schools table is a small, largely closed set of parishes (design.md 10.3
lists canonical/alias forms, source namespace, and suffix conventions --
there is no per-school "chronology" or "collision" concept the way there is
for an athlete's races). :func:`score_school_candidate` covers that.
"""

from __future__ import annotations

from dataclasses import dataclass

from xc_platform.db.repositories.canonical import (
    AthleteRecord,
    AthleteSeasonRecord,
    SchoolRecord,
)
from xc_platform.resolution.normalization import (
    normalize_name_token,
    normalize_school_key,
)

# Evidence/conflict codes mirror the resolution agent's contract vocabulary
# (design.md section 10.5) so a deterministic proposal and a future agent
# proposal are comparable, and a human reviewer sees one consistent code
# set regardless of which step produced the case.
EV_EXACT_NORMALIZED_FIRST_NAME = "EXACT_NORMALIZED_FIRST_NAME"
EV_SAME_LAST_NAME_ONLY = "SAME_LAST_NAME_ONLY"
EV_SAME_SCHOOL_SEASON = "SAME_SCHOOL_SEASON"
EV_GRADE_PROGRESSION_PLAUSIBLE = "GRADE_PROGRESSION_PLAUSIBLE"
EV_BIB_HISTORY_MATCH = "BIB_HISTORY_MATCH"
EV_GENDER_CONSISTENT = "GENDER_CONSISTENT"
EV_PARTIAL_NORMALIZED_TOKENS = "PARTIAL_NORMALIZED_TOKENS"

CONFLICT_SAME_RACE_COLLISION = "SAME_RACE_COLLISION"
CONFLICT_CONFIRMED_DISTINCT = "CONFIRMED_DISTINCT"
CONFLICT_SOURCE_ID_BOUND_ELSEWHERE = "SOURCE_ID_CONFLICT"
CONFLICT_IMPOSSIBLE_GRADE_CHRONOLOGY = "IMPOSSIBLE_GRADE_CHRONOLOGY"
CONFLICT_LOCKED_DECISION = "LOCKED_DECISION_CONFLICT"
# A gender mismatch is evidence, not identity (design.md 10.3) -- it is
# deliberately absent from _HARD_CONFLICTS below and only lowers score.
CONFLICT_GENDER_MISMATCH = "GENDER_MISMATCH"

_HARD_CONFLICTS = frozenset(
    {
        CONFLICT_SAME_RACE_COLLISION,
        CONFLICT_CONFIRMED_DISTINCT,
        CONFLICT_SOURCE_ID_BOUND_ELSEWHERE,
        CONFLICT_IMPOSSIBLE_GRADE_CHRONOLOGY,
        CONFLICT_LOCKED_DECISION,
    }
)


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_id: str
    score: float
    evidence_codes: tuple[str, ...]
    conflict_codes: tuple[str, ...]

    @property
    def is_hard_blocked(self) -> bool:
        return any(code in _HARD_CONFLICTS for code in self.conflict_codes)


@dataclass(frozen=True, slots=True)
class AthleteResolutionInput:
    """One incoming result's athlete-identifying fields, already parsed
    from :class:`~xc_platform.ingest.contract.StagedResultInput.candidate_fields`."""

    first_name: str
    last_name: str
    school_id: str | None
    season_year: int
    grade: int | None
    gender_code: str | None
    bib: str | None
    race_id: str


def grade_progression_conflict(
    known: list[tuple[int, int]], new_season_year: int, new_grade: int
) -> bool:
    """True if adding ``(new_season_year, new_grade)`` to a candidate's
    known ``(season_year, grade)`` history is impossible under a simple
    non-decreasing-grade-with-year rule (design.md 10.4).

    Allows one grade of slack in either direction (a student is never
    expected to repeat more than one grade or skip more than two) rather
    than requiring an exact +1-per-year progression, which would flag
    ordinary retention/acceleration as a hard conflict.
    """
    for season_year, grade in known:
        year_gap = new_season_year - season_year
        grade_gap = new_grade - grade
        if year_gap == 0:
            if grade_gap != 0:
                return True
        elif year_gap > 0:
            if grade_gap < -1 or grade_gap > year_gap + 1:
                return True
        else:
            if grade_gap > 1 or grade_gap < year_gap - 1:
                return True
    return False


def score_athlete_candidate(
    candidate: AthleteRecord,
    incoming: AthleteResolutionInput,
    *,
    seasons: list[AthleteSeasonRecord],
    has_result_in_race: bool,
    has_bib_match: bool,
    confirmed_distinct: bool,
    source_id_bound_elsewhere: bool,
) -> Candidate:
    evidence: list[str] = []
    conflicts: list[str] = []

    exact_first = normalize_name_token(incoming.first_name) == normalize_name_token(
        candidate.canonical_first_name
    )
    evidence.append(
        EV_EXACT_NORMALIZED_FIRST_NAME if exact_first else EV_SAME_LAST_NAME_ONLY
    )

    same_school_season = incoming.school_id is not None and any(
        s.season_year == incoming.season_year and s.school_id == incoming.school_id
        for s in seasons
    )
    if same_school_season:
        evidence.append(EV_SAME_SCHOOL_SEASON)

    known_grade_pairs = [
        (s.season_year, s.grade) for s in seasons if s.grade is not None
    ]
    if incoming.grade is not None and known_grade_pairs:
        if grade_progression_conflict(
            known_grade_pairs, incoming.season_year, incoming.grade
        ):
            conflicts.append(CONFLICT_IMPOSSIBLE_GRADE_CHRONOLOGY)
        else:
            evidence.append(EV_GRADE_PROGRESSION_PLAUSIBLE)

    known_genders = {s.gender_code for s in seasons}
    if incoming.gender_code is not None and known_genders:
        if incoming.gender_code in known_genders:
            evidence.append(EV_GENDER_CONSISTENT)
        else:
            conflicts.append(CONFLICT_GENDER_MISMATCH)

    if has_bib_match:
        evidence.append(EV_BIB_HISTORY_MATCH)
    if has_result_in_race:
        conflicts.append(CONFLICT_SAME_RACE_COLLISION)
    if confirmed_distinct:
        conflicts.append(CONFLICT_CONFIRMED_DISTINCT)
    if source_id_bound_elsewhere:
        conflicts.append(CONFLICT_SOURCE_ID_BOUND_ELSEWHERE)

    score = 0.0
    if exact_first:
        score += 0.5
    if same_school_season:
        score += 0.25
    if EV_GRADE_PROGRESSION_PLAUSIBLE in evidence:
        score += 0.15
    if has_bib_match:
        score += 0.2
    if CONFLICT_GENDER_MISMATCH in conflicts:
        score -= 0.2
    score = max(0.0, min(1.0, score))

    return Candidate(
        entity_id=candidate.athlete_id,
        score=score,
        evidence_codes=tuple(evidence),
        conflict_codes=tuple(conflicts),
    )


def score_school_candidate(candidate: SchoolRecord, raw_school_name: str) -> Candidate:
    """Token-overlap similarity between an incoming raw school name and an
    existing canonical school -- used only when steps 1-3 found no exact
    alias/key match, to decide whether the incoming name is close enough
    to an existing school to warrant review rather than a blind create."""
    incoming_tokens = set(normalize_school_key(raw_school_name).split())
    candidate_tokens = set(normalize_school_key(candidate.canonical_name).split())
    if not incoming_tokens or not candidate_tokens:
        overlap = 0.0
    else:
        shared = incoming_tokens & candidate_tokens
        overlap = len(shared) / max(len(incoming_tokens), len(candidate_tokens))

    evidence = (EV_PARTIAL_NORMALIZED_TOKENS,) if overlap > 0 else ()
    return Candidate(
        entity_id=candidate.school_id,
        score=overlap,
        evidence_codes=evidence,
        conflict_codes=(),
    )
