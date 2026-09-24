from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from xc_platform.ingest.adapters.runsignup import (
    RunSignupAdapter,
    UnsupportedRunSignupUrlError,
    classify_event,
    discover,
    fetch,
    normalize,
    normalize_gender,
    normalize_runsignup_url,
    season_year_from_start_time,
)
from xc_platform.ingest.adapters.runsignup_client import RequestBudget, RunSignupClient
from xc_platform.ingest.contract import (
    DiscoveredResultSet,
    DiscoveryRequest,
    FetchResult,
    FrozenSeasonRefusedError,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "feasibility"
    / "runsignup_get_results.json"
)


# --- URL normalization ------------------------------------------------


def test_normalizes_a_plain_results_url() -> None:
    normalized = normalize_runsignup_url("https://runsignup.com/Race/Results/154050")
    assert normalized.race_id == "154050"
    assert normalized.requested_result_set_id is None
    assert normalized.requested_per_page is None
    assert normalized.source_namespace == "runsignup"


def test_rewrites_fragment_scope_into_structured_fields() -> None:
    normalized = normalize_runsignup_url(
        "https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100"
    )
    assert normalized.race_id == "154050"
    assert normalized.requested_result_set_id == 691534
    assert normalized.requested_per_page == 100


def test_accepts_a_state_city_prefixed_url() -> None:
    normalized = normalize_runsignup_url(
        "https://runsignup.com/Race/VA/Alexandria/Results/154050"
    )
    assert normalized.race_id == "154050"


def test_accepts_mirror_hosts() -> None:
    for host in ("trisignup.com", "www.trisignup.com", "adventuresignup.com"):
        normalized = normalize_runsignup_url(f"https://{host}/Race/Results/154708")
        assert normalized.race_id == "154708"


def test_rejects_a_non_runsignup_host() -> None:
    with pytest.raises(UnsupportedRunSignupUrlError, match="RunSignup-family"):
        normalize_runsignup_url("https://evil.example.com/Race/Results/154050")


def test_rejects_a_non_results_url() -> None:
    with pytest.raises(UnsupportedRunSignupUrlError):
        normalize_runsignup_url("https://runsignup.com/Race/154050")


# --- small helpers ------------------------------------------------------


def test_normalize_gender_variants() -> None:
    assert normalize_gender("F") == "F"
    assert normalize_gender("Girls") == "F"
    assert normalize_gender("M") == "M"
    assert normalize_gender("Boys") == "M"
    assert normalize_gender(None) is None
    assert normalize_gender("") is None
    assert normalize_gender("unknown") is None


def test_classify_event_division_and_gender() -> None:
    assert classify_event("Frosh Girls (3rd-4th Grade)") == ("Frosh", "F")
    assert classify_event("Varsity Boys") == ("Varsity", "M")
    assert classify_event("JV Girls") == ("JV", "F")
    assert classify_event("2nd Grade Boys") == ("2nd Grade", "M")
    assert classify_event("2nd Boys") == ("2nd Grade", "M")
    assert classify_event("Something Unrecognized") == (None, None)


def test_season_year_from_start_time() -> None:
    assert season_year_from_start_time("9/21/2024 09:00") == 2024
    assert season_year_from_start_time(None) is None
    assert season_year_from_start_time("garbage") is None


# --- discover -------------------------------------------------------------


def _scripted_client(responses: dict[str, dict[str, Any]]) -> RunSignupClient:
    def fetcher(url: str) -> Any:
        from dataclasses import dataclass

        @dataclass(frozen=True, slots=True)
        class _Resp:
            status: int
            body: bytes

        for prefix, payload in responses.items():
            if prefix in url:
                return _Resp(status=200, body=json.dumps(payload).encode("utf-8"))
        raise AssertionError(f"unscripted URL: {url}")

    return RunSignupClient(
        budget=RequestBudget(max_requests=1000, max_wall_time_s=60.0), fetcher=fetcher
    )


def test_discover_enumerates_events_and_marks_frozen_seasons() -> None:
    race_meta = {
        "race": {
            "name": "NVJCYO Meet 1",
            "events": [
                {"event_id": 1, "name": "Varsity Boys", "start_time": "9/1/2024"},
                {"event_id": 2, "name": "Varsity Boys", "start_time": "9/1/2026"},
            ],
        }
    }
    result_sets_2024 = {
        "individual_results_sets": [
            {
                "individual_result_set_id": 10,
                "individual_result_set_name": "Varsity Boys 2024",
                "public_results": "T",
                "preliminary_results": "F",
            }
        ]
    }
    result_sets_2026 = {
        "individual_results_sets": [
            {
                "individual_result_set_id": 20,
                "individual_result_set_name": "Varsity Boys 2026",
                "public_results": "T",
                "preliminary_results": "F",
            }
        ]
    }
    client = _scripted_client(
        {
            "race/154050?format": race_meta,
            "get-result-sets?format=json&event_id=1": result_sets_2024,
            "get-result-sets?format=json&event_id=2": result_sets_2026,
        }
    )
    normalized = normalize_runsignup_url("https://runsignup.com/Race/Results/154050")
    result = discover(
        client, DiscoveryRequest(normalized_url=normalized, correlation_id="c1")
    )

    assert len(result.result_sets) == 2
    by_year = {rs.season_year: rs for rs in result.result_sets}
    assert by_year[2024].frozen_season is True
    assert by_year[2026].frozen_season is False
    assert by_year[2024].meet_number == 1  # race 154050 -> meet 1, static config
    assert by_year[2024].division == "Varsity"
    assert by_year[2024].gender_code == "M"
    assert by_year[2024].distance_meters == 4000

    assert len(result.frozen) == 1
    assert len(result.importable) == 1


# --- fetch: frozen-season refusal ------------------------------------------


def _importable_item(**overrides: Any) -> DiscoveredResultSet:
    base: dict[str, Any] = dict(
        source_namespace="runsignup",
        race_id="154050",
        event_id=1,
        event_name="Varsity Boys",
        set_id=10,
        set_name="Varsity Boys",
        season_year=2026,
        meet_number=1,
        division="Varsity",
        gender_code="M",
        distance_meters=4000,
        public_results=True,
        preliminary_results=False,
        frozen_season=False,
    )
    base.update(overrides)
    return DiscoveredResultSet(**base)


def test_fetch_refuses_a_frozen_season_item() -> None:
    client = _scripted_client({})
    frozen_item = _importable_item(season_year=2024, frozen_season=True)
    with pytest.raises(FrozenSeasonRefusedError, match="2024"):
        fetch(client, frozen_item)


def test_fetch_pages_until_a_short_page_and_reports_completion() -> None:
    page1 = {
        "individual_results_sets": [
            {
                "results_headers": {"result_id": "Result Id", "place": "Place"},
                "results": [{"result_id": i, "place": i} for i in range(1, 3)],
            }
        ]
    }
    page2 = {
        "individual_results_sets": [
            {
                "results_headers": {"result_id": "Result Id", "place": "Place"},
                "results": [{"result_id": 3, "place": 3}],
            }
        ]
    }
    client = _scripted_client(
        {
            "results_per_page=2&page=1": page1,
            "results_per_page=2&page=2": page2,
        }
    )
    item = _importable_item()
    result = fetch(client, item, per_page=2, max_pages=10)
    assert result.pages_fetched == 2
    assert len(result.raw_rows) == 3
    assert result.content_sha256  # non-empty


# --- normalize: against the real redacted contract fixture -----------------


@pytest.fixture
def fixture_fetch_result() -> FetchResult:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    block = payload["individual_results_sets"][0]
    item = _importable_item()
    return FetchResult(
        item=item,
        raw_rows=tuple(block["results"]),
        headers=block["results_headers"],
        pages_fetched=1,
        content_sha256="test",
        retrieved_at="2026-09-22T00:00:00Z",
    )


def test_normalize_maps_every_row_from_the_real_fixture(
    fixture_fetch_result: FetchResult,
) -> None:
    staged = normalize(fixture_fetch_result)
    assert len(staged) == len(fixture_fetch_result.raw_rows) == 6

    first = staged[0]
    assert first.source_result_id == "900000001"
    assert first.idempotency_key == "runsignup:900000001"
    assert first.candidate_fields["athlete_full_name"] == "Avery Alpha"
    assert first.candidate_fields["team_name"] == "St Example"
    assert first.candidate_fields["grade"] == 4  # from custom-field-649068 "Year"
    assert first.candidate_fields["place_overall"] == 1
    assert first.candidate_fields["scored_flag"] == "scored"
    # clock_time "8:27.42" -> ms; chip_time is blank in this fixture.
    assert first.candidate_fields["finish_time_ms"] == (8 * 60 + 27.42) * 1000
    assert first.candidate_fields["original_time_text"] == "8:27.42"
    assert first.is_valid


def test_normalize_field_mapping_is_by_label_not_by_numeric_id(
    fixture_fetch_result: FetchResult,
) -> None:
    """The fixture's custom-field IDs are arbitrary; only the label matters."""
    relabeled_headers = dict(fixture_fetch_result.headers)
    # Same semantic fields, deliberately different numeric IDs than the
    # fixture uses -- this must not change the normalized output at all.
    relabeled_rows = []
    for row in fixture_fetch_result.raw_rows:
        relabeled_rows.append(
            {
                **{k: v for k, v in row.items() if not k.startswith("custom-field-")},
                "custom-field-999001": row["custom-field-649068"],
                "custom-field-999002": row["custom-field-649069"],
                "custom-field-999003": row["custom-field-649073"],
            }
        )
    relabeled_headers = {
        k: v for k, v in relabeled_headers.items() if not k.startswith("custom-field-")
    }
    relabeled_headers.update(
        {
            "custom-field-999001": "Year",
            "custom-field-999002": "Team Name",
            "custom-field-999003": "Scored",
        }
    )
    relabeled = FetchResult(
        item=fixture_fetch_result.item,
        raw_rows=tuple(relabeled_rows),
        headers=relabeled_headers,
        pages_fetched=1,
        content_sha256="test",
        retrieved_at="2026-09-22T00:00:00Z",
    )
    original = normalize(fixture_fetch_result)
    remapped = normalize(relabeled)
    for o, r in zip(original, remapped, strict=True):
        assert o.candidate_fields["grade"] == r.candidate_fields["grade"]
        assert o.candidate_fields["team_name"] == r.candidate_fields["team_name"]
        assert o.candidate_fields["scored_flag"] == r.candidate_fields["scored_flag"]


def test_normalize_flags_a_missing_name_and_falls_back_to_content_hash_key() -> None:
    item = _importable_item()
    payload = FetchResult(
        item=item,
        raw_rows=(
            {
                "result_id": None,
                "place": 1,
                "first_name": "",
                "last_name": "",
                "gender": "F",
                "clock_time": "9:00.00",
                "chip_time": "",
            },
        ),
        headers={},
        pages_fetched=1,
        content_sha256="test",
        retrieved_at="2026-09-22T00:00:00Z",
    )
    staged = normalize(payload)
    assert len(staged) == 1
    row = staged[0]
    assert row.source_result_id is None
    assert row.idempotency_key.startswith("runsignup:contenthash:")
    assert not row.is_valid
    assert any("missing first_name or last_name" in w for w in row.validation_warnings)


def test_normalize_preserves_original_time_text_and_raw_fields(
    fixture_fetch_result: FetchResult,
) -> None:
    staged = normalize(fixture_fetch_result)
    for staged_row, raw_row in zip(staged, fixture_fetch_result.raw_rows, strict=True):
        assert staged_row.raw_fields == raw_row


def test_normalize_is_idempotent_across_repeat_calls(
    fixture_fetch_result: FetchResult,
) -> None:
    """Task 8.4: repeat fetching must yield stable idempotency keys."""
    first_pass = normalize(fixture_fetch_result)
    second_pass = normalize(fixture_fetch_result)
    assert [r.idempotency_key for r in first_pass] == [
        r.idempotency_key for r in second_pass
    ]
    assert [r.source_result_id for r in first_pass] == [
        r.source_result_id for r in second_pass
    ]


# --- discover/fetch edge cases (Task 8.4) -----------------------------


def test_discover_preserves_the_preliminary_results_flag() -> None:
    race_meta = {
        "race": {
            "events": [
                {"event_id": 1, "name": "Varsity Boys", "start_time": "9/1/2026"},
            ],
        }
    }
    result_sets = {
        "individual_results_sets": [
            {
                "individual_result_set_id": 10,
                "individual_result_set_name": "Varsity Boys (Preliminary)",
                "public_results": "T",
                "preliminary_results": "T",
            }
        ]
    }
    client = _scripted_client(
        {
            "race/154050?format": race_meta,
            "get-result-sets?format=json&event_id=1": result_sets,
        }
    )
    normalized = normalize_runsignup_url("https://runsignup.com/Race/Results/154050")
    result = discover(
        client, DiscoveryRequest(normalized_url=normalized, correlation_id="c1")
    )
    assert result.result_sets[0].preliminary_results is True


def test_fetch_handles_a_team_only_result_set_with_zero_rows() -> None:
    """A published set with zero rows is a real source state (design.md
    section 9.3, "team-only sets are legitimate"), not a fetch failure."""
    empty_page = {
        "individual_results_sets": [
            {"results_headers": {"result_id": "Result Id"}, "results": []}
        ]
    }
    client = _scripted_client({"get-results?format=json": empty_page})
    item = _importable_item()
    result = fetch(client, item, per_page=25, max_pages=10)
    assert result.raw_rows == ()
    assert result.pages_fetched == 1
    staged = normalize(result)
    assert staged == []


def test_fetch_handles_a_genuinely_empty_response_body() -> None:
    """The very first page returning no individual_results_sets block at all
    (not even an empty one) must also end the fetch cleanly, not loop."""
    client = _scripted_client({"get-results?format=json": {}})
    item = _importable_item()
    result = fetch(client, item, per_page=25, max_pages=10)
    assert result.raw_rows == ()
    assert result.pages_fetched == 1


# --- the adapter's Protocol surface -----------------------------------


def test_adapter_can_handle_matches_source_namespace() -> None:
    client = _scripted_client({})
    adapter = RunSignupAdapter(client=client)
    normalized = normalize_runsignup_url("https://runsignup.com/Race/Results/154050")
    assert adapter.can_handle(normalized)
