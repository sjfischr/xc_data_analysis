"""/api/v1/system/version reports real schema versions (2026-10-18)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from xc_platform.db.migrator import latest_schema_version


def test_version_reports_the_live_and_supported_schema(client: TestClient) -> None:
    body = client.get("/api/v1/system/version").json()
    assert body["supported_schema_version"] == latest_schema_version()
    assert isinstance(body["schema_version"], int)
    assert body["schema_version"] <= body["supported_schema_version"]
    assert body["publication_id"]
