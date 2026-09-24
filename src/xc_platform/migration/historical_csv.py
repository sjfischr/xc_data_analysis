"""Read and normalize the frozen historical baseline CSV (Task 5.1).

Reads ``data/merged/season_results.csv`` (or an equivalent frozen export)
without modifying it, and turns each row into a :class:`HistoricalRow` that
carries both the untouched raw fields (for provenance -- Requirement 7.1)
and normalized candidate values (for canonical writing).

Division-to-distance and gender-string normalization mirror
``add_distance_metrics.py`` exactly, so recomputing pace/speed in
:mod:`xc_platform.migration.views` reproduces the historical baseline's own
numbers (Requirement 1.3).
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Kilometers by division, exactly as add_distance_metrics.py defined them.
DIVISION_DISTANCE_METERS: dict[str, int] = {
    "2nd Grade": 2000,
    "Frosh": 2000,
    "JV": 3000,
    "Varsity": 4000,
}

# The frozen baseline never normalized gender before storing it, so both
# raw spellings appear (Requirement 1.8: migrated exactly as the baseline
# holds it). Both map to the same schema-level gender_code.
_GENDER_MAP: dict[str, str] = {
    "M": "M",
    "Boys": "M",
    "F": "F",
    "Girls": "F",
}

# The reserved placeholder school for a result whose source row has no team
# name (Requirement 1.7: represent the gap explicitly, never invent a team).
# This exact string mirrors scripts/freeze_baseline.py's own
# ``team_name.fillna("Unknown")`` convention for the Saint Sebastian
# calculation, so a migrated row and the frozen baseline agree on what an
# absent team is called.
UNKNOWN_SCHOOL_NAME = "Unknown"


@dataclass(frozen=True, slots=True)
class HistoricalRow:
    """One normalized row from the frozen baseline CSV.

    ``raw`` is the complete, unmodified source row (every CSV column),
    preserved verbatim for staging provenance. Every other field is a
    normalized candidate value, or ``None`` when the source value is blank,
    unparseable, or the row is a known non-individual artifact (see
    ``is_quarantine_candidate``).
    """

    row_index: int
    raw: dict[str, str]

    season_year: int | None
    meet_number: int | None
    meet_series: str | None
    meet_name: str | None
    division: str | None
    gender_code: str | None
    distance_meters: int | None

    athlete_full_name: str | None
    team_name: str | None
    bib: str | None
    grade: int | None
    place_overall: int | None
    finish_time_ms: int | None
    original_time_text: str | None
    scored_flag: str

    quarantine_reason: str | None = field(default=None)

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine_reason is not None


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped if stripped else None


def _parse_int(value: str) -> int | None:
    text = _blank_to_none(value)
    if text is None:
        return None
    try:
        return round(float(text))
    except ValueError:
        return None


def _parse_bib(value: str) -> str | None:
    text = _blank_to_none(value)
    if text is None:
        return None
    try:
        return str(round(float(text)))
    except ValueError:
        return text


def _parse_finish_time_ms(value: str) -> int | None:
    text = _blank_to_none(value)
    if text is None:
        return None
    try:
        seconds = float(text)
    except ValueError:
        return None
    return round(seconds * 1000)


def normalize_gender(value: str) -> str | None:
    text = _blank_to_none(value)
    if text is None:
        return None
    return _GENDER_MAP.get(text)


def _normalize_scored_flag(value: str) -> str:
    text = _blank_to_none(value)
    return "scored" if text == "*" else "unknown"


def parse_row(row_index: int, raw: dict[str, str]) -> HistoricalRow:
    """Normalize one raw CSV row (as a str->str mapping) into a HistoricalRow."""
    season_year = _parse_int(raw.get("season_year", ""))
    meet_number = _parse_int(raw.get("meet_number", ""))
    division = _blank_to_none(raw.get("division", ""))
    gender_code = normalize_gender(raw.get("gender", ""))
    athlete_full_name = _blank_to_none(raw.get("athlete_full_name", ""))
    team_name = _blank_to_none(raw.get("team_name", ""))

    quarantine_reason: str | None = None
    if athlete_full_name is None:
        # The documented 2023 Meet 2 gap (Requirement 1.10): a team-only
        # scoring artifact with no per-athlete rows at all. It stays a gap
        # and is never filled from any source, including this migration.
        quarantine_reason = (
            "no athlete_full_name in source row -- team-only scoring "
            "artifact, not an individual result (Requirement 1.7, 1.10). "
            f"raw team_name={team_name!r}, season_year={season_year!r}, "
            f"meet_number={meet_number!r}, division={division!r}."
        )
    elif None in (season_year, meet_number, division, gender_code):
        # Required for canonical meet/race identity (Requirement 7.2: a
        # required field is validated before resolution). None of these are
        # ever missing in the frozen baseline -- verified when this
        # migration ran against it -- so this is a defensive path for any
        # future frozen export, not a case observed in practice.
        quarantine_reason = (
            "missing a required field (season_year, meet_number, division, "
            f"or gender): season_year={season_year!r}, "
            f"meet_number={meet_number!r}, division={division!r}, "
            f"gender_code={gender_code!r}."
        )

    return HistoricalRow(
        row_index=row_index,
        raw=raw,
        season_year=season_year,
        meet_number=meet_number,
        meet_series=_blank_to_none(raw.get("meet_series", "")),
        meet_name=_blank_to_none(raw.get("meet_name", "")),
        division=division,
        gender_code=gender_code,
        distance_meters=DIVISION_DISTANCE_METERS.get(division) if division else None,
        athlete_full_name=athlete_full_name,
        team_name=team_name,
        bib=_parse_bib(raw.get("bib", "")),
        grade=_parse_int(raw.get("grade", "")),
        place_overall=_parse_int(raw.get("place_overall", "")),
        finish_time_ms=_parse_finish_time_ms(raw.get("finish_time_s", "")),
        original_time_text=_blank_to_none(raw.get("finish_time_str", "")),
        scored_flag=_normalize_scored_flag(raw.get("Scored", "")),
        quarantine_reason=quarantine_reason,
    )


def iter_rows(csv_path: str | Path) -> Iterator[HistoricalRow]:
    """Yield every row of the frozen baseline CSV as a :class:`HistoricalRow`.

    Reads the file without modifying it (Task 5.1). ``encoding="utf-8"``
    is explicit and deliberate: some historical team names contain a
    non-breaking space (U+00A0) that a prior standardization pass's mapping
    table (keyed on a plain space) never matched, so they survive in the
    frozen CSV as their own distinct strings. That is preserved here
    verbatim, not corrected -- Requirement 1.8/1.9.
    """
    with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_index, raw in enumerate(reader):
            yield parse_row(row_index, raw)
