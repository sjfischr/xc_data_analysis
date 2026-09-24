from __future__ import annotations

from fastapi.testclient import TestClient

from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.publications import PublicationRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def _publish(ctx: AppContext) -> None:
    """Republish the current writer db as a new generation so read
    endpoints (which pin a snapshot fetched from the publisher, never the
    live writer connection directly) can see rows just written -- the
    same checkpoint-then-publish sequence run_local_api.py's own seeding
    uses."""
    ctx.writer_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    parent = PublicationRepository(ctx.writer_conn).get_active()
    manifest = ctx.snapshot_publisher.publish(
        ctx.db_path,
        created_by="test-seed",
        ingest_run_id="test-seed",
        summary={},
        parent_publication_id=parent.publication_id if parent else None,
    )
    PublicationRepository(ctx.writer_conn).record_publication(
        publication_id=manifest.publication_id,
        snapshot_key=manifest.snapshot_key,
        snapshot_version_id=manifest.snapshot_version_id,
        content_sha256=manifest.sha256,
        byte_size=manifest.byte_size,
        schema_version=manifest.schema_version,
        summary_json="{}",
    )


def _seed_team_scores(
    ctx: AppContext, *, season_year: int = 2026, timed: bool = True
) -> None:
    write = CanonicalWriteRepository(ctx.writer_conn)
    historical = HistoricalCanonicalWriter(ctx.writer_conn)
    source_id = StagingRepository(ctx.writer_conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(ctx.writer_conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    school_id = write.create_school(canonical_name="St Agnes")
    meet_id = historical.get_or_create_meet(
        season_year=season_year, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    for place in range(1, 6):
        athlete_id = write.create_athlete(display_name=f"Runner {place}")
        historical.insert_result(
            source_id=source_id,
            race_id=race_id,
            athlete_id=athlete_id,
            school_id=school_id,
            ingest_run_id=ingest_run_id,
            finish_time_ms=1_200_000 + place * 1000 if timed else None,
            original_time_text=None,
            place_overall=place,
            bib=None,
            grade=None,
            scored_flag="scored",
        )
    _publish(ctx)


def test_team_scores_endpoint_returns_real_seeded_data(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")

    response = client.get("/api/v1/team-scores")

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    entry = body["data"][0]
    assert entry["school_display_name"] == "St Agnes"
    assert entry["score"] == 15
    assert entry["team_rank"] == 1
    assert body["publication_id"]


def test_team_scores_endpoint_filters_by_season_year(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx, season_year=2026)
    login(client, role="viewer")

    response = client.get("/api/v1/team-scores", params={"season_year": 2025})

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_team_scores_endpoint_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/team-scores")
    assert response.status_code == 401


def test_saint_sebastian_standings_endpoint_returns_real_seeded_data(
    client: TestClient, ctx: AppContext
) -> None:
    write = CanonicalWriteRepository(ctx.writer_conn)
    historical = HistoricalCanonicalWriter(ctx.writer_conn)
    source_id = StagingRepository(ctx.writer_conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(ctx.writer_conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    school_id = write.create_school(canonical_name="St Agnes")
    athlete_id = write.create_athlete(display_name="Always Runs")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    historical.insert_result(
        source_id=source_id,
        race_id=race_id,
        athlete_id=athlete_id,
        school_id=school_id,
        ingest_run_id=ingest_run_id,
        finish_time_ms=500_000,
        original_time_text=None,
        place_overall=1,
        bib=None,
        grade=None,
        scored_flag="scored",
    )
    _publish(ctx)
    login(client, role="viewer")

    response = client.get("/api/v1/saint-sebastian-standings")

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["athlete_display_name"] == "Always Runs"
    assert body["data"][0]["standing_rank"] == 1


def test_saint_sebastian_standings_endpoint_requires_a_session(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/saint-sebastian-standings")
    assert response.status_code == 401


def test_team_scores_endpoint_filters_by_school_id(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    write = CanonicalWriteRepository(ctx.writer_conn)
    write.create_school(canonical_name="Some Other School")
    _publish(ctx)
    login(client, role="viewer")

    all_scores = client.get("/api/v1/team-scores").json()["data"]
    school_id = all_scores[0]["school_id"]

    filtered = client.get(
        "/api/v1/team-scores", params={"school_id": school_id}
    ).json()["data"]
    assert len(filtered) == 1
    assert filtered[0]["school_id"] == school_id
    assert filtered[0]["team_rank"] == all_scores[0]["team_rank"]


def test_school_roster_endpoint_returns_real_seeded_data(
    client: TestClient, ctx: AppContext
) -> None:
    write = CanonicalWriteRepository(ctx.writer_conn)
    historical = HistoricalCanonicalWriter(ctx.writer_conn)
    source_id = StagingRepository(ctx.writer_conn).get_or_create_source(
        source_namespace="runsignup",
        adapter_type="runsignup",
        base_domain="runsignup.com",
    )
    ingest_run_id = IngestRunRepository(ctx.writer_conn).create_run(
        source_id=source_id,
        submitted_url="https://runsignup.com/Race/Results/1",
        adapter_type="runsignup",
        correlation_id="corr-1",
    )
    school_id = write.create_school(canonical_name="St Agnes")
    athlete_id = write.create_athlete(display_name="Jane Doe")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name=None, series="NVJCYO"
    )
    race_id = historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )
    historical.insert_result(
        source_id=source_id,
        race_id=race_id,
        athlete_id=athlete_id,
        school_id=school_id,
        ingest_run_id=ingest_run_id,
        finish_time_ms=500_000,
        original_time_text=None,
        place_overall=1,
        bib=None,
        grade=None,
        scored_flag="scored",
    )
    historical.upsert_athlete_season(
        athlete_id=athlete_id,
        season_year=2026,
        school_id=school_id,
        grade=6,
        gender_code="F",
    )
    _publish(ctx)
    login(client, role="viewer")

    response = client.get(f"/api/v1/schools/{school_id}/roster")

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["athlete_display_name"] == "Jane Doe"


def test_school_roster_endpoint_404s_for_an_unknown_school(client: TestClient) -> None:
    login(client, role="viewer")
    response = client.get("/api/v1/schools/does-not-exist/roster")
    assert response.status_code == 404


def test_school_roster_endpoint_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/schools/some-id/roster")
    assert response.status_code == 401


def test_results_endpoint_returns_rows_with_pace_and_speed(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")

    body = client.get("/api/v1/results", params={"season_year": 2026}).json()

    assert len(body["data"]) == 5
    first = body["data"][0]
    assert first["school_display_name"] == "St Agnes"
    assert first["place_overall"] == 1
    # 1201 s over 5000 m (3.107 mi) is 386.5 s/mi.
    assert abs(first["pace_seconds_per_mile"] - 386.55) < 0.1
    assert first["speed_mph"] > 9
    assert body["publication_id"]


def test_results_endpoint_filters_by_repeated_meet_number(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")

    kept = client.get("/api/v1/results", params=[("meet_number", 1)]).json()
    dropped = client.get(
        "/api/v1/results", params=[("meet_number", 2), ("meet_number", 3)]
    ).json()

    assert len(kept["data"]) == 5
    assert dropped["data"] == []


def test_overview_endpoint_reports_metrics_and_leaderboards(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")

    data = client.get("/api/v1/overview").json()["data"]

    assert data["metrics"] == {
        "athletes": 5,
        "schools": 1,
        "meets": 1,
        "seasons": 1,
        "results": 5,
        "athletes_with_progress": 0,
    }
    assert [r["place_overall"] for r in data["top_placements"]] == [1, 2, 3, 4, 5]
    assert data["fastest_pace"][0]["athlete_display_name"] == "Runner 1"
    assert data["most_improved"] == []


def test_athlete_profile_endpoint_returns_identity_and_results(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")
    athlete_id = client.get("/api/v1/results").json()["data"][0]["athlete_id"]

    data = client.get(f"/api/v1/athletes/{athlete_id}/profile").json()["data"]

    assert data["display_name"] == "Runner 1"
    assert data["summary"]["races"] == 1
    assert data["summary"]["best_place"] == 1
    assert data["summary"]["best_time_ms"] == 1_201_000
    assert len(data["results"]) == 1
    assert data["pace_trend"] is None


def test_athlete_profile_endpoint_404s_an_unknown_athlete(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")
    assert client.get("/api/v1/athletes/nope/profile").status_code == 404


def test_school_profile_endpoint_returns_seasons_scores_and_top_athletes(
    client: TestClient, ctx: AppContext
) -> None:
    _seed_team_scores(ctx)
    login(client, role="viewer")
    school_id = client.get("/api/v1/results").json()["data"][0]["school_id"]

    data = client.get(f"/api/v1/schools/{school_id}/profile").json()["data"]

    assert data["display_name"] == "St Agnes"
    assert data["seasons"] == [{"season_year": 2026, "athletes": 5, "results": 5}]
    assert data["team_scores"][0]["score"] == 15
    assert data["top_athletes"][0]["athlete_display_name"] == "Runner 1"


def test_new_dashboard_endpoints_require_a_session(client: TestClient) -> None:
    for path in (
        "/api/v1/results",
        "/api/v1/overview",
        "/api/v1/athletes/x/profile",
        "/api/v1/schools/x/profile",
    ):
        assert client.get(path).status_code == 401


def test_team_scores_for_places_without_times_do_not_500(
    client: TestClient, ctx: AppContext
) -> None:
    """Found in visual QA, 2026-09-23: some 2023 races recorded places but
    no finish times, so ``avg_time_s`` is NULL and the non-optional schema
    field turned ``/team-scores`` and the school profile into a 500."""
    _seed_team_scores(ctx, timed=False)
    login(client, role="viewer")

    scores = client.get("/api/v1/team-scores")
    assert scores.status_code == 200
    assert scores.json()["data"][0]["avg_time_s"] is None
    school_id = scores.json()["data"][0]["school_id"]
    assert client.get(f"/api/v1/schools/{school_id}/profile").status_code == 200
