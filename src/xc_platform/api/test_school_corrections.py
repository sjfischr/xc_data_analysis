"""Owner correction: renaming a school (Task 19.3)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext
from xc_platform.api.test_catalog import _seed_team_scores
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository

REASON = "St. John the Beloved, McLean VA (Diocese of Arlington)"


def _school_id(client: TestClient, name: str) -> str:
    [school] = client.get("/api/v1/schools", params={"q": name}).json()["data"]
    return str(school["school_id"])


def test_rename_keeps_results_and_publishes_the_new_name(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="admin")
    school_id = _school_id(client, "St Agnes")

    response = client.post(
        f"/api/v1/schools/{school_id}/rename",
        json={"name": "St John the Beloved", "reason": REASON},
    )

    assert response.status_code == 200, response.text
    assert response.json()["publication_id"]
    profile = client.get(f"/api/v1/schools/{school_id}/profile").json()["data"]
    assert profile["display_name"] == "St John the Beloved"
    assert profile["seasons"][0]["results"] == 5
    [score] = client.get("/api/v1/team-scores").json()["data"]
    assert score["school_display_name"] == "St John the Beloved"


def test_rename_is_recorded_with_actor_reason_and_before_after(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="admin")
    school_id = _school_id(client, "St Agnes")
    client.post(
        f"/api/v1/schools/{school_id}/rename",
        json={"name": "St John the Beloved", "reason": REASON},
    )

    row = ctx.writer_conn.execute(
        "SELECT d.actor, d.evidence_summary, d.affected_records_json "
        "FROM resolution_decisions d JOIN resolution_cases c "
        "USING (resolution_case_id) WHERE c.candidate_entity_id = ?",
        (school_id,),
    ).fetchone()
    assert row["actor"].startswith("admin:")
    assert row["evidence_summary"] == REASON
    assert '"from_display_name": "St Agnes"' in row["affected_records_json"]
    # A correction never leaves an open review question behind.
    assert client.get("/api/v1/resolution-cases").json()["data"] == []


def test_rename_to_an_existing_school_name_is_a_conflict(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    CanonicalWriteRepository(ctx.writer_conn).create_school(
        canonical_name="Holy Family"
    )
    login(client, role="admin")
    school_id = _school_id(client, "St Agnes")

    response = client.post(
        f"/api/v1/schools/{school_id}/rename",
        json={"name": "Holy Family", "reason": REASON},
    )
    assert response.status_code == 409


def test_viewers_cannot_rename(client: TestClient, ctx: AppContext) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")
    school_id = _school_id(client, "St Agnes")
    response = client.post(
        f"/api/v1/schools/{school_id}/rename",
        json={"name": "Anything", "reason": REASON},
    )
    assert response.status_code == 403
