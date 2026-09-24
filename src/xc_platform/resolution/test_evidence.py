from __future__ import annotations

from xc_platform.db.repositories.canonical import (
    AthleteRecord,
    AthleteSeasonRecord,
    SchoolRecord,
)
from xc_platform.resolution.evidence import (
    CONFLICT_CONFIRMED_DISTINCT,
    CONFLICT_GENDER_MISMATCH,
    CONFLICT_IMPOSSIBLE_GRADE_CHRONOLOGY,
    CONFLICT_SAME_RACE_COLLISION,
    EV_EXACT_NORMALIZED_FIRST_NAME,
    EV_SAME_LAST_NAME_ONLY,
    EV_SAME_SCHOOL_SEASON,
    AthleteResolutionInput,
    grade_progression_conflict,
    score_athlete_candidate,
    score_school_candidate,
)


def _athlete(first: str, last: str, athlete_id: str = "a1") -> AthleteRecord:
    return AthleteRecord(
        athlete_id=athlete_id,
        canonical_first_name=first,
        canonical_last_name=last,
        display_name=f"{first} {last}",
        status="active",
    )


def _season(
    *,
    season_year: int,
    school_id: str = "school-1",
    grade: int | None,
    gender_code: str = "F",
) -> AthleteSeasonRecord:
    return AthleteSeasonRecord(
        athlete_season_id="s1",
        athlete_id="a1",
        season_year=season_year,
        school_id=school_id,
        grade=grade,
        gender_code=gender_code,
    )


# --- grade_progression_conflict --------------------------------------------


def test_grade_progression_allows_normal_advancement() -> None:
    assert not grade_progression_conflict([(2025, 5)], 2026, 6)


def test_grade_progression_allows_one_grade_of_slack() -> None:
    assert not grade_progression_conflict([(2025, 5)], 2026, 5)  # retained
    assert not grade_progression_conflict([(2025, 5)], 2027, 8)  # skipped ahead


def test_grade_progression_flags_impossible_regression() -> None:
    assert grade_progression_conflict([(2025, 8)], 2026, 3)


def test_grade_progression_flags_disagreement_within_same_season() -> None:
    assert grade_progression_conflict([(2026, 5)], 2026, 8)


# --- score_athlete_candidate -------------------------------------------


def test_exact_first_name_and_same_school_season_score_highest() -> None:
    candidate = _athlete("Gwendolyn", "Fischer")
    incoming = AthleteResolutionInput(
        first_name="Gwendolyn",
        last_name="Fischer",
        school_id="school-1",
        season_year=2026,
        grade=6,
        gender_code="F",
        bib=None,
        race_id="race-1",
    )
    result = score_athlete_candidate(
        candidate,
        incoming,
        seasons=[_season(season_year=2026, grade=6)],  # earlier race, same season
        has_result_in_race=False,
        has_bib_match=False,
        confirmed_distinct=False,
        source_id_bound_elsewhere=False,
    )
    assert EV_EXACT_NORMALIZED_FIRST_NAME in result.evidence_codes
    assert EV_SAME_SCHOOL_SEASON in result.evidence_codes
    assert result.score > 0.7
    assert not result.is_hard_blocked


def test_different_first_name_is_weak_evidence_only() -> None:
    candidate = _athlete("Giovanni", "Smolinski")
    incoming = AthleteResolutionInput(
        first_name="Gianna",
        last_name="Smolinski",
        school_id=None,
        season_year=2026,
        grade=None,
        gender_code=None,
        bib=None,
        race_id="race-1",
    )
    result = score_athlete_candidate(
        candidate,
        incoming,
        seasons=[],
        has_result_in_race=False,
        has_bib_match=False,
        confirmed_distinct=False,
        source_id_bound_elsewhere=False,
    )
    assert EV_SAME_LAST_NAME_ONLY in result.evidence_codes
    assert result.score < 0.3


def test_same_race_collision_is_a_hard_conflict() -> None:
    candidate = _athlete("Gwendolyn", "Fischer")
    incoming = AthleteResolutionInput(
        first_name="Gwendolyn",
        last_name="Fischer",
        school_id=None,
        season_year=2026,
        grade=None,
        gender_code=None,
        bib=None,
        race_id="race-1",
    )
    result = score_athlete_candidate(
        candidate,
        incoming,
        seasons=[],
        has_result_in_race=True,
        has_bib_match=False,
        confirmed_distinct=False,
        source_id_bound_elsewhere=False,
    )
    assert CONFLICT_SAME_RACE_COLLISION in result.conflict_codes
    assert result.is_hard_blocked


def test_confirmed_distinct_is_a_hard_conflict() -> None:
    candidate = _athlete("Giovanni", "Smolinski")
    incoming = AthleteResolutionInput(
        first_name="Gianna",
        last_name="Smolinski",
        school_id=None,
        season_year=2026,
        grade=None,
        gender_code=None,
        bib=None,
        race_id="race-1",
    )
    result = score_athlete_candidate(
        candidate,
        incoming,
        seasons=[],
        has_result_in_race=False,
        has_bib_match=False,
        confirmed_distinct=True,
        source_id_bound_elsewhere=False,
    )
    assert CONFLICT_CONFIRMED_DISTINCT in result.conflict_codes
    assert result.is_hard_blocked


def test_impossible_grade_chronology_is_a_hard_conflict() -> None:
    candidate = _athlete("Gwendolyn", "Fischer")
    incoming = AthleteResolutionInput(
        first_name="Gwendolyn",
        last_name="Fischer",
        school_id=None,
        season_year=2026,
        grade=2,
        gender_code=None,
        bib=None,
        race_id="race-1",
    )
    result = score_athlete_candidate(
        candidate,
        incoming,
        seasons=[_season(season_year=2025, grade=8)],
        has_result_in_race=False,
        has_bib_match=False,
        confirmed_distinct=False,
        source_id_bound_elsewhere=False,
    )
    assert CONFLICT_IMPOSSIBLE_GRADE_CHRONOLOGY in result.conflict_codes
    assert result.is_hard_blocked


def test_gender_mismatch_lowers_score_but_never_hard_blocks() -> None:
    candidate = _athlete("Gwendolyn", "Fischer")
    incoming = AthleteResolutionInput(
        first_name="Gwendolyn",
        last_name="Fischer",
        school_id=None,
        season_year=2026,
        grade=None,
        gender_code="M",
        bib=None,
        race_id="race-1",
    )
    result = score_athlete_candidate(
        candidate,
        incoming,
        seasons=[_season(season_year=2025, grade=None, gender_code="F")],
        has_result_in_race=False,
        has_bib_match=False,
        confirmed_distinct=False,
        source_id_bound_elsewhere=False,
    )
    assert CONFLICT_GENDER_MISMATCH in result.conflict_codes
    assert (
        not result.is_hard_blocked
    )  # design.md 10.3: gender is evidence, not identity


# --- score_school_candidate -------------------------------------------


def test_school_candidate_scores_token_overlap() -> None:
    school = SchoolRecord(
        school_id="school-1",
        canonical_name="St Agnes",
        display_name="St Agnes",
        status="active",
    )
    result = score_school_candidate(school, "St. Agnes Parish")
    assert result.score == 1.0  # "st agnes" both ways once "parish" is dropped


def test_school_candidate_scores_zero_for_unrelated_name() -> None:
    school = SchoolRecord(
        school_id="school-1",
        canonical_name="St Agnes",
        display_name="St Agnes",
        status="active",
    )
    result = score_school_candidate(school, "Holy Family")
    assert result.score == 0.0
