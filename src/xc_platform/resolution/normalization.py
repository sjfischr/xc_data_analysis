"""Candidate-key normalization for entity resolution (Task 10.1, design.md
section 10.2).

Every function here returns a folded *comparison key*, never a value to
store or display: originals are always preserved untouched in
``*_aliases.raw_value`` and canonical ``display_name``/``canonical_name``
columns (Requirement 8.1). Nicknames are deliberately NOT folded together
here -- "Gwen" and "Gwendolyn" normalize to different keys unless an
approved alias or evidence-backed decision says otherwise (design.md 10.2).
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[.,'\-]")
# Saint/parish conventions already established as an approved decision in
# the frozen baseline (migration/aliases.py's LEGACY_TEAM_NAME_MAPPING):
# "St."/"Saint" fold together, and "Parish" is a source-name suffix, not
# part of a school's identity.
_SAINT_RE = re.compile(r"\bsaint\b")
_PARISH_SUFFIX_RE = re.compile(r"\bparish\b")


def _fold(value: str) -> str:
    """Unicode-normalize, strip accents, case-fold, and collapse whitespace
    and punctuation to single spaces."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_accents.casefold()
    no_punctuation = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", no_punctuation).strip()


def normalize_name_token(value: str) -> str:
    """Fold one name token (a first or last name) to a comparison key."""
    return _fold(value)


def athlete_candidate_key(first_name: str, last_name: str) -> str:
    """The comparison key for resolution order step 3 (exact normalized
    canonical key): both tokens must match exactly, so this is not used to
    seed the candidate pool itself (that's last-name-only; see
    :meth:`~xc_platform.db.repositories.canonical.CanonicalReadRepository.find_athletes_by_normalized_last_name`)."""
    return f"{normalize_name_token(first_name)}|{normalize_name_token(last_name)}"


def normalize_school_key(value: str) -> str:
    """Fold a school/parish name, applying the approved saint/parish
    conventions on top of the shared fold (design.md 10.2)."""
    folded = _fold(value)
    folded = _SAINT_RE.sub("st", folded)
    folded = _PARISH_SUFFIX_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", folded).strip()
