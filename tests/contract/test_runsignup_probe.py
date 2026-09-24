"""Contract tests for the Task 3.2 RunSignup probe.

These run in ordinary CI and must never touch the network: they exercise the
pure normalization/classification logic against the redacted fixture captured
during the live feasibility run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xc_platform.feasibility.runsignup import (
    ResultSetProbe,
    canonical_gender,
    classify_event,
    compare_with_baseline,
    normalize_results_url,
    season_year,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "feasibility"
    / "runsignup_get_results.json"
)
SUPPLIED_URL = (
    "https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100"
)


def test_supplied_url_scope_is_recovered_from_the_fragment() -> None:
    normalized = normalize_results_url(SUPPLIED_URL)
    assert normalized["race_id"] == "154050"
    assert normalized["requested_result_set_id"] == 691534
    assert normalized["requested_per_page"] == 100
    assert normalized["fragment_present"] is True


def test_url_without_fragment_has_no_requested_scope() -> None:
    normalized = normalize_results_url("https://runsignup.com/Race/Results/154708")
    assert normalized["race_id"] == "154708"
    assert normalized["requested_result_set_id"] is None
    assert normalized["requested_per_page"] is None


def test_city_state_url_form_is_accepted() -> None:
    normalized = normalize_results_url(
        "https://runsignup.com/Race/VA/Lorton/Results/154050"
    )
    assert normalized["race_id"] == "154050"


@pytest.mark.parametrize(
    "url",
    [
        "http://runsignup.com/Race/Results/154050",  # not HTTPS
        "https://example.com/Race/Results/154050",  # wrong host
        "https://runsignup.com/Race/Results/",  # no race id
    ],
)
def test_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_results_url(url)


@pytest.mark.parametrize(
    ("name", "division", "gender"),
    [
        ("Varsity Girls (7th - 8th Grade)", "Varsity", "F"),
        ("Varsity Boys (7th - 8th Grade)", "Varsity", "M"),
        ("JV Girls (5th - 6th Grade)", "JV", "F"),
        ("Frosh Boys (3rd-4th Grade)", "Frosh", "M"),
        ("2nd Grade Girls", "2nd Grade", "F"),
        # The source also publishes the shortened spelling.
        ("2nd Boys", "2nd Grade", "M"),
    ],
)
def test_event_names_map_onto_the_baseline_vocabulary(
    name: str, division: str, gender: str
) -> None:
    assert classify_event(name) == (division, gender)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("F", "F"), ("M", "M"), ("Girls", "F"), ("Boys", "M"), ("girls", "F")],
)
def test_gender_labels_canonicalize(raw: str, expected: str) -> None:
    assert canonical_gender(raw) == expected


def test_unknown_gender_label_is_not_guessed() -> None:
    assert canonical_gender("unspecified") is None
    assert canonical_gender(None) is None


def test_season_year_is_read_from_the_source_timestamp() -> None:
    assert season_year("9/28/2024 08:00") == 2024
    assert season_year(None) is None
    assert season_year("no date here") is None


def test_fixture_exposes_athlete_level_rows_and_labeled_custom_fields() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    block = payload["individual_results_sets"][0]
    headers = block["results_headers"]
    rows = block["results"]

    assert rows, "fixture must contain athlete-level rows"
    for row in rows:
        assert row["first_name"] and row["last_name"]
        assert row["result_id"]
        assert row["clock_time"] or row["chip_time"]

    custom = {k: v for k, v in headers.items() if k.startswith("custom-field-")}
    assert custom, "fixture must exercise custom fields"
    # The adapter contract: map by header label, never by numeric field id.
    assert "Team Name" in custom.values()


def _probe(division: str, gender: str, named: int) -> ResultSetProbe:
    return ResultSetProbe(
        race_id="154050",
        meet_number=1,
        season_year=2024,
        event_id=1,
        event_name=f"{division} {gender}",
        division=division,
        gender=gender,
        distance="4K",
        set_id=1,
        set_name="set",
        public_results=True,
        preliminary_results=False,
        results_source_name=None,
        header_labels={},
        custom_field_labels={},
        row_count=named,
        unique_result_ids=named,
        pages_fetched=1,
        last_page_rows=named,
        place_min=1,
        place_max=named,
        athlete_level=named > 0,
        named_rows=named,
        timed_rows=named,
    )


def test_baseline_comparison_folds_mixed_gender_encodings(tmp_path: Path) -> None:
    """Boys/Girls and F/M must compare as the same cell, not as four cells."""
    baseline = {
        "total_records": 20,
        "by_season_meet_division_gender": [
            {
                "season_year": 2024,
                "meet_number": 1,
                "division": "Varsity",
                "gender": "Girls",
                "records": 12,
            },
            {
                "season_year": 2024,
                "meet_number": 1,
                "division": "Varsity",
                "gender": "M",
                "records": 8,
            },
        ],
    }
    path = tmp_path / "counts.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    comparison = compare_with_baseline(
        [_probe("Varsity", "F", 12), _probe("Varsity", "M", 8)], path
    )

    assert comparison["cells_differing"] == 0
    assert comparison["cells_matching"] == 2
    assert comparison["baseline_gender_encoding"]["labels_by_season_meet"] == {
        "2024-M1": ["Girls", "M"]
    }


def test_baseline_comparison_reports_real_differences(tmp_path: Path) -> None:
    baseline = {
        "total_records": 10,
        "by_season_meet_division_gender": [
            {
                "season_year": 2024,
                "meet_number": 1,
                "division": "Varsity",
                "gender": "F",
                "records": 10,
            }
        ],
    }
    path = tmp_path / "counts.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")

    comparison = compare_with_baseline([_probe("Varsity", "F", 12)], path)

    assert comparison["cells_differing"] == 1
    assert comparison["cells"][0]["delta"] == 2
