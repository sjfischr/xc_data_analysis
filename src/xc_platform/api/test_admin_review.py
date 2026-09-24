"""Ingest-time review decisions and the admin intake views (Task 19.3):
"match to existing" and "create new" resolve a pending case with an alias,
so the next commit applies the row; the preview and run list show what an
administrator is about to publish."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext
from xc_platform.api.test_admin_ingest import URL, _script
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository


def _submit(client: TestClient) -> str:
    response = client.post("/api/v1/ingest-runs", json={"url": URL})
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["ingest_run_id"])


def _two_jane_does(ctx: AppContext) -> tuple[str, str]:
    """Two existing athletes named Jane Doe make the incoming "Jane Doe"
    ambiguous, so resolution opens a review case instead of matching."""
    write = CanonicalWriteRepository(ctx.writer_conn)
    write.create_school(canonical_name="St Agnes")
    return (
        write.create_athlete(display_name="Jane Doe"),
        write.create_athlete(display_name="Jane Doe"),
    )


def test_case_view_explains_the_raw_name_and_candidates(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    first, second = _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    run_id = _submit(client)

    [case] = client.get(
        "/api/v1/resolution-cases", params={"ingest_run_id": run_id}
    ).json()["data"]

    assert case["entity_type"] == "athlete"
    assert case["raw_name"] == "Jane Doe"
    assert case["context"] == "St Agnes"
    assert case["can_decide"] is True
    assert {c["entity_id"] for c in case["candidates"]} == {first, second}


def test_match_to_existing_lets_the_commit_apply_the_row(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    chosen, _ = _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    run_id = _submit(client)
    [case] = client.get("/api/v1/resolution-cases").json()["data"]

    blocked = client.post(f"/api/v1/ingest-runs/{run_id}/commit")
    assert blocked.status_code == 409

    decision = client.post(
        f"/api/v1/resolution-cases/{case['resolution_case_id']}/decisions",
        params={"decision_type": "match", "entity_id": chosen},
    )
    assert decision.status_code == 200, decision.text

    commit = client.post(f"/api/v1/ingest-runs/{run_id}/commit")
    assert commit.status_code == 200, commit.text
    assert commit.json()["data"]["inserted_count"] == 1
    canonical = CanonicalReadRepository(ctx.writer_conn)
    [row] = canonical.list_result_rows(athlete_id=chosen)
    assert row.season_year == 2026
    # The matched athlete gains this season's roster row at commit.
    assert [s.season_year for s in canonical.list_athlete_seasons(chosen)] == [2026]


def test_create_new_makes_a_third_athlete(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    first, second = _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    run_id = _submit(client)
    [case] = client.get("/api/v1/resolution-cases").json()["data"]

    decision = client.post(
        f"/api/v1/resolution-cases/{case['resolution_case_id']}/decisions",
        params={"decision_type": "create_new"},
    )
    assert decision.status_code == 200, decision.text
    new_id = decision.json()["data"]["entity_id"]
    assert new_id not in (first, second)

    assert client.post(f"/api/v1/ingest-runs/{run_id}/commit").status_code == 200
    rows = CanonicalReadRepository(ctx.writer_conn).list_result_rows(athlete_id=new_id)
    assert len(rows) == 1


def test_match_requires_an_existing_entity(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    _submit(client)
    [case] = client.get("/api/v1/resolution-cases").json()["data"]
    url = f"/api/v1/resolution-cases/{case['resolution_case_id']}/decisions"

    assert client.post(url, params={"decision_type": "match"}).status_code == 422
    missing = client.post(url, params={"decision_type": "match", "entity_id": "nope"})
    assert missing.status_code == 404


def test_run_list_and_preview_show_what_will_be_published(
    client: TestClient, scripted_client_factory: Any
) -> None:
    login(client, role="admin")
    _script(scripted_client_factory)
    run_id = _submit(client)

    [run] = client.get("/api/v1/ingest-runs").json()["data"]
    assert run["ingest_run_id"] == run_id
    assert run["submitted_url"] == URL

    preview = client.get(f"/api/v1/ingest-runs/{run_id}/preview").json()["data"]
    assert preview["counts"] == {"valid": 1}
    assert preview["races"] == [
        {
            "season_year": 2026,
            "meet_number": 1,
            "division_code": "Varsity",
            "gender_code": "F",
            "distance_meters": preview["races"][0]["distance_meters"],
            "rows": 1,
        }
    ]
    assert preview["sample_rows"][0]["athlete"] == "Jane Doe"


def test_viewers_cannot_reach_intake(client: TestClient) -> None:
    login(client, role="viewer")
    assert client.get("/api/v1/ingest-runs").status_code == 403


def test_resolve_endpoint_reruns_resolution_without_duplicating_cases(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    run_id = _submit(client)

    first = client.post(f"/api/v1/ingest-runs/{run_id}/resolve")
    second = client.post(f"/api/v1/ingest-runs/{run_id}/resolve")

    assert first.status_code == 200, first.text
    assert first.json()["data"]["pending_cases"] == 1
    assert second.json()["data"]["pending_cases"] == 1


def test_create_new_retry_reuses_an_alias_left_by_a_failed_attempt(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    """Regression (local testing, 2026-09-24): an attempt that wrote the
    athlete and alias but not the decision made every retry a 500 and left
    another orphan athlete each time."""
    import json

    from xc_platform.migration.aliases import normalize_alias_value

    _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    _submit(client)
    [case] = client.get("/api/v1/resolution-cases").json()["data"]
    record = ctx.resolver.get_case(case["resolution_case_id"])
    assert record is not None
    evidence = json.loads(record.evidence_json)
    write = CanonicalWriteRepository(ctx.writer_conn)
    half_done = write.create_athlete(display_name="Jane Doe")
    write.add_athlete_alias(
        athlete_id=half_done,
        source_id=evidence["source_id"],
        raw_value="Jane Doe",
        normalized_value=normalize_alias_value("Jane Doe"),
        context_key=evidence["context_key"],
    )
    athletes_before = ctx.writer_conn.execute(
        "SELECT COUNT(*) FROM athletes"
    ).fetchone()[0]

    retry = client.post(
        f"/api/v1/resolution-cases/{case['resolution_case_id']}/decisions",
        params={"decision_type": "create_new"},
    )

    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["entity_id"] == half_done
    athletes_after = ctx.writer_conn.execute(
        "SELECT COUNT(*) FROM athletes"
    ).fetchone()[0]
    assert athletes_after == athletes_before


def test_concurrent_decisions_and_listings_do_not_break_the_writer(
    client: TestClient, scripted_client_factory: Any, ctx: AppContext
) -> None:
    """Regression: a case-list reload overlapping a decision on the shared
    writer connection raised 'bad parameter or other API misuse' and left
    the decision half-applied."""
    from concurrent.futures import ThreadPoolExecutor

    _two_jane_does(ctx)
    login(client, role="admin")
    _script(scripted_client_factory)
    run_id = _submit(client)
    [case] = client.get("/api/v1/resolution-cases").json()["data"]

    def decide() -> int:
        return client.post(
            f"/api/v1/resolution-cases/{case['resolution_case_id']}/decisions",
            params={"decision_type": "create_new"},
        ).status_code

    def browse() -> int:
        return client.get(
            "/api/v1/resolution-cases", params={"ingest_run_id": run_id}
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(browse) for _ in range(12)]
        futures.insert(6, pool.submit(decide))
        codes = [f.result() for f in futures]

    assert 500 not in codes
    assert client.get("/api/v1/resolution-cases").json()["data"] == []
