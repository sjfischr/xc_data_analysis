from __future__ import annotations

from xc_platform.resolution.normalization import (
    athlete_candidate_key,
    normalize_name_token,
    normalize_school_key,
)


def test_normalize_name_token_folds_case_and_whitespace() -> None:
    assert normalize_name_token("  Mckinley  ") == "mckinley"
    assert normalize_name_token("McKinley") == normalize_name_token("Mckinley")


def test_normalize_name_token_strips_accents() -> None:
    assert normalize_name_token("Calderón") == normalize_name_token("Calderon")


def test_normalize_name_token_does_not_fold_nicknames() -> None:
    # Nicknames are only equivalent via an approved alias/decision, not
    # implicit key folding (design.md 10.2).
    assert normalize_name_token("Gwen") != normalize_name_token("Gwendolyn")


def test_athlete_candidate_key_is_order_sensitive_pair() -> None:
    assert athlete_candidate_key("Lucy", "DeMarr") == athlete_candidate_key(
        "Lucy", "Demarr"
    )
    assert athlete_candidate_key("Lucy", "DeMarr") != athlete_candidate_key(
        "Luke", "DeMarr"
    )


def test_normalize_school_key_folds_saint_and_drops_parish_suffix() -> None:
    assert normalize_school_key("St. Agnes Parish") == normalize_school_key(
        "Saint Agnes"
    )
    assert normalize_school_key("St. Agnes Parish") == "st agnes"


def test_normalize_school_key_folds_punctuation_to_whitespace() -> None:
    assert normalize_school_key("O'Leary Academy") == normalize_school_key(
        "O Leary Academy"
    )
