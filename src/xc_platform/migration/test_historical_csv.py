from __future__ import annotations

from xc_platform.migration.historical_csv import (
    DIVISION_DISTANCE_METERS,
    parse_row,
)


def _row(**overrides: str) -> dict[str, str]:
    base = {
        "season_year": "2024",
        "meet_number": "1",
        "meet_series": "NVJCYO Cross Country Developmental",
        "meet_name": "NVJCYO Cross Country Developmental Meet 1",
        "division": "Frosh",
        "gender": "Boys",
        "athlete_full_name": "Teddy Cypher",
        "team_name": "St Agnes",
        "bib": "308.0",
        "grade": "4.0",
        "place_overall": "1.0",
        "finish_time_s": "529.99",
        "finish_time_str": "8:49.99",
        "Scored": "",
    }
    base.update(overrides)
    return base


def test_parses_a_normal_row() -> None:
    row = parse_row(0, _row())
    assert row.season_year == 2024
    assert row.meet_number == 1
    assert row.division == "Frosh"
    assert row.gender_code == "M"
    assert row.distance_meters == DIVISION_DISTANCE_METERS["Frosh"]
    assert row.athlete_full_name == "Teddy Cypher"
    assert row.team_name == "St Agnes"
    assert row.bib == "308"
    assert row.grade == 4
    assert row.place_overall == 1
    assert row.finish_time_ms == 529990
    assert row.original_time_text == "8:49.99"
    assert row.scored_flag == "unknown"
    assert not row.is_quarantined


def test_scored_flag_from_asterisk() -> None:
    row = parse_row(0, _row(Scored="*"))
    assert row.scored_flag == "scored"


def test_gender_normalization_variants() -> None:
    assert parse_row(0, _row(gender="Boys")).gender_code == "M"
    assert parse_row(0, _row(gender="M")).gender_code == "M"
    assert parse_row(0, _row(gender="Girls")).gender_code == "F"
    assert parse_row(0, _row(gender="F")).gender_code == "F"


def test_missing_athlete_name_is_quarantined() -> None:
    row = parse_row(0, _row(athlete_full_name=""))
    assert row.is_quarantined
    assert row.quarantine_reason is not None
    assert "team-only" in row.quarantine_reason


def test_missing_team_name_is_not_quarantined() -> None:
    row = parse_row(0, _row(team_name=""))
    assert not row.is_quarantined
    assert row.team_name is None


def test_null_finish_time_and_place_are_preserved_as_none_not_quarantined() -> None:
    row = parse_row(0, _row(finish_time_s="", place_overall=""))
    assert not row.is_quarantined
    assert row.finish_time_ms is None
    assert row.place_overall is None


def test_raw_dict_is_preserved_verbatim() -> None:
    raw = _row()
    row = parse_row(0, raw)
    assert row.raw == raw
    assert row.raw is raw
