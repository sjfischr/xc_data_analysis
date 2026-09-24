"""Admin ingest endpoint tests (Task 13.3): the real workflow, driven
through the API, using the same scripted-client fixture pattern as
ingest/test_workflow.py.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository

RACE_ID = "154050"
URL = f"https://runsignup.com/Race/Results/{RACE_ID}"


def _race_meta() -> dict[str, Any]:
    return {
        "race": {
            "name": "NVJCYO Meet 1",
            "events": [
                {"event_id": 1, "name": "Varsity Girls", "start_time": "9/1/2026"}
            ],
        }
    }


_RESULT_SETS = {
    "individual_results_sets": [
        {
            "individual_result_set_id": 10,
            "individual_result_set_name": "Varsity Girls",
            "public_results": "T",
            "preliminary_results": "F",
        }
    ]
}


def _results_page() -> dict[str, Any]:
    return {
        "individual_results_sets": [
            {
                "results_headers": {"custom-field-100001": "Team Name"},
                "results": [
                    {
                        "result_id": 5001,
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "gender": "F",
                        "place": "1",
                        "chip_time": "21:30",
                        "bib": "101",
                        "custom-field-100001": "St Agnes",
                    }
                ],
            }
        ]
    }


def _script(scripted_client_factory: Any) -> None:
    scripted_client_factory.responses = {
        f"race/{RACE_ID}?format": _race_meta(),
        "get-result-sets?format=json&event_id=1": _RESULT_SETS,
        "get-results?format=json": _results_page(),
    }


def test_submit_then_commit_produces_a_real_result_and_publication(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    _script(scripted_client_factory)

    submit = client.post("/api/v1/ingest-runs", json={"url": URL})
    assert submit.status_code == 200, submit.text
    run_id = submit.json()["data"]["ingest_run_id"]
    assert submit.json()["data"]["state"] == "awaiting_review"

    commit = client.post(f"/api/v1/ingest-runs/{run_id}/commit")
    assert commit.status_code == 200, commit.text
    body = commit.json()
    assert body["data"]["inserted_count"] == 1
    assert body["publication_id"]

    status = client.get(f"/api/v1/ingest-runs/{run_id}")
    assert status.json()["data"]["state"] == "committed"


def test_repeated_commit_is_idempotent(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    _script(scripted_client_factory)

    run_id = client.post("/api/v1/ingest-runs", json={"url": URL}).json()["data"][
        "ingest_run_id"
    ]
    first = client.post(f"/api/v1/ingest-runs/{run_id}/commit").json()
    second = client.post(f"/api/v1/ingest-runs/{run_id}/commit").json()

    assert second["data"]["already_committed"] is True
    assert second["publication_id"] == first["publication_id"]


def test_unsupported_url_is_a_conflict_not_a_500(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    response = client.post(
        "/api/v1/ingest-runs", json={"url": "https://example.com/not-runsignup"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_resolution_case_listing_and_reject_decision(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    # A row with no team name opens a school resolution case instead of
    # auto-resolving (resolve_scope's no-team-name branch).
    ambiguous_rows = _results_page()
    ambiguous_rows["individual_results_sets"][0]["results"][0][
        "custom-field-100001"
    ] = ""
    scripted_client_factory.responses = {
        f"race/{RACE_ID}?format": _race_meta(),
        "get-result-sets?format=json&event_id=1": _RESULT_SETS,
        "get-results?format=json": ambiguous_rows,
    }

    submit = client.post("/api/v1/ingest-runs", json={"url": URL})
    assert submit.status_code == 200, submit.text

    cases = client.get("/api/v1/resolution-cases")
    assert cases.status_code == 200
    case_list = cases.json()["data"]
    assert len(case_list) == 1
    case_id = case_list[0]["resolution_case_id"]

    decision = client.post(
        f"/api/v1/resolution-cases/{case_id}/decisions",
        params={
            "decision_type": "reject",
            "actor": "admin-1",
            "evidence_summary": "no team name; keep separate",
        },
    )
    assert decision.status_code == 200, decision.text

    cases_after = client.get("/api/v1/resolution-cases").json()["data"]
    assert cases_after == []


def _open_a_pending_case(client: TestClient, scripted_client_factory: Any) -> str:
    ambiguous_rows = _results_page()
    ambiguous_rows["individual_results_sets"][0]["results"][0][
        "custom-field-100001"
    ] = ""
    scripted_client_factory.responses = {
        f"race/{RACE_ID}?format": _race_meta(),
        "get-result-sets?format=json&event_id=1": _RESULT_SETS,
        "get-results?format=json": ambiguous_rows,
    }
    submit = client.post("/api/v1/ingest-runs", json={"url": URL})
    assert submit.status_code == 200, submit.text
    case_list = client.get("/api/v1/resolution-cases").json()["data"]
    assert len(case_list) == 1
    return str(case_list[0]["resolution_case_id"])


def test_resolution_case_approve_decision_merges_the_loser_athlete(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    login(client, role="admin")
    write = CanonicalWriteRepository(ctx.writer_conn)
    winner_id = write.create_athlete(display_name="Winner Athlete")
    loser_id = write.create_athlete(display_name="Loser Athlete")
    case_id = _open_a_pending_case(client, scripted_client_factory)

    decision = client.post(
        f"/api/v1/resolution-cases/{case_id}/decisions",
        params={
            "decision_type": "approve",
            "actor": "admin-1",
            "evidence_summary": "same person, merge",
            "winner_athlete_id": winner_id,
            "loser_athlete_id": loser_id,
        },
    )

    assert decision.status_code == 200, decision.text
    canonical = CanonicalReadRepository(ctx.writer_conn)
    merged = canonical.get_athlete(loser_id)
    assert merged is not None
    assert merged.status == "merged"


def test_resolution_case_approve_decision_without_ids_is_a_422(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    case_id = _open_a_pending_case(client, scripted_client_factory)

    decision = client.post(
        f"/api/v1/resolution-cases/{case_id}/decisions",
        params={
            "decision_type": "approve",
            "actor": "admin-1",
            "evidence_summary": "same person, merge",
        },
    )

    assert decision.status_code == 422
    assert decision.json()["error"]["code"] == "validation_error"


def test_resolution_case_approve_decision_with_unknown_athlete_id_is_a_404(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    login(client, role="admin")
    write = CanonicalWriteRepository(ctx.writer_conn)
    winner_id = write.create_athlete(display_name="Winner Athlete")
    case_id = _open_a_pending_case(client, scripted_client_factory)

    decision = client.post(
        f"/api/v1/resolution-cases/{case_id}/decisions",
        params={
            "decision_type": "approve",
            "actor": "admin-1",
            "evidence_summary": "same person, merge",
            "winner_athlete_id": winner_id,
            "loser_athlete_id": "does-not-exist",
        },
    )

    assert decision.status_code == 404


def test_resolution_case_decision_on_an_already_decided_case_is_a_409(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    case_id = _open_a_pending_case(client, scripted_client_factory)

    first = client.post(
        f"/api/v1/resolution-cases/{case_id}/decisions",
        params={
            "decision_type": "reject",
            "actor": "admin-1",
            "evidence_summary": "no team name; keep separate",
        },
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/resolution-cases/{case_id}/decisions",
        params={
            "decision_type": "reject",
            "actor": "admin-1",
            "evidence_summary": "deciding again",
        },
    )

    assert second.status_code == 409
