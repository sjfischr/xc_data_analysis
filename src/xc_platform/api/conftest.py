from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from xc_platform.api.app import create_app
from xc_platform.api.context import AppContext, RunSignupClientFactory
from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import FakeS3Client
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.publications import PublicationRepository
from xc_platform.ingest.adapters.runsignup_client import RequestBudget, RunSignupClient
from xc_platform.ingest.raw_storage import RawObjectStore
from xc_platform.security.connection_ticket import InMemoryTicketReplayStore
from xc_platform.security.session import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    InMemorySessionStore,
)


class _ScriptedClientFactory(RunSignupClientFactory):
    """Test double: builds a fresh RunSignupClient over caller-supplied
    scripted responses, mirroring the pattern in ingest/test_workflow.py."""

    def __init__(self) -> None:
        self.responses: dict[str, dict[str, Any]] = {}

    def __call__(self) -> RunSignupClient:
        responses = self.responses

        def fetcher(url: str) -> Any:
            import json
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
            budget=RequestBudget(max_requests=1000, max_wall_time_s=60.0),
            fetcher=fetcher,
        )


@pytest.fixture
def scripted_client_factory() -> _ScriptedClientFactory:
    return _ScriptedClientFactory()


class _ScriptedModelFactory:
    """Test double: builds a fresh FakeModel from the currently-set
    script each time it's called (a real BedrockModel would similarly be
    stateless across requests)."""

    def __init__(self) -> None:
        self.script: list[Any] = []
        self.created: list[Any] = []

    def __call__(self) -> Any:
        from xc_platform.agents._fake_model import FakeModel

        model = FakeModel(script=list(self.script))
        self.created.append(model)
        return model


@pytest.fixture
def scripted_model_factory() -> _ScriptedModelFactory:
    return _ScriptedModelFactory()


@pytest.fixture
def ctx(
    tmp_path: Path,
    scripted_client_factory: _ScriptedClientFactory,
    scripted_model_factory: _ScriptedModelFactory,
) -> Iterator[AppContext]:
    db_path = tmp_path / "xc.db"
    conn = open_writer_connection(db_path)
    migrate(conn)

    s3 = FakeS3Client()
    publisher = SnapshotPublisher(s3, work_dir=tmp_path / "pub-work", owner_id="test")
    # An initial (empty) publication so read endpoints have something to
    # pin. Recorded in db_publications too, exactly as commit_scope does
    # for a real commit -- otherwise commit_scope's own get_active() parent
    # lookup finds nothing locally even though S3's active.json already
    # exists, and its next publish wrongly treats itself as a bootstrap.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    seed_manifest = publisher.publish(
        db_path, created_by="test-setup", ingest_run_id="seed", summary={}
    )
    PublicationRepository(conn).record_publication(
        publication_id=seed_manifest.publication_id,
        snapshot_key=seed_manifest.snapshot_key,
        snapshot_version_id=seed_manifest.snapshot_version_id,
        content_sha256=seed_manifest.sha256,
        byte_size=seed_manifest.byte_size,
        schema_version=seed_manifest.schema_version,
        summary_json="{}",
    )

    context = AppContext(
        db_path=db_path,
        writer_conn=conn,
        snapshot_reader=SnapshotReader(s3, cache_dir=tmp_path / "cache"),
        snapshot_publisher=publisher,
        raw_store=RawObjectStore(s3, work_dir=tmp_path / "raw-work"),
        runsignup_client_factory=scripted_client_factory,
        session_store=InMemorySessionStore(),
        ticket_signing_key=b"test-ticket-signing-key",
        ticket_replay_store=InMemoryTicketReplayStore(),
        actor_id_application_key=b"test-actor-key",
        analytics_model_factory=scripted_model_factory,
    )
    try:
        yield context
    finally:
        conn.close()


@pytest.fixture
def client(ctx: AppContext) -> TestClient:
    # Session/CSRF cookies are Secure (Requirement 13.6): a plain http://
    # test base_url would set but never re-send them, since Secure cookies
    # are only sent back over an origin the client considers HTTPS.
    return TestClient(create_app(ctx), base_url="https://testserver")


def login(
    client: TestClient,
    *,
    subject: str = "user-1",
    role: str = "viewer",
    agent_access: bool = False,
) -> TestClient:
    response = client.post(
        "/api/v1/auth/dev-login",
        params={"subject": subject, "role": role, "agent_access": agent_access},
    )
    assert response.status_code == 200, response.text
    # Echo the CSRF cookie into a default header, exactly as a real
    # frontend would read the (non-HttpOnly) CSRF cookie and attach it to
    # every mutating request (deps.py's double-submit check).
    csrf_token = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token, "login did not set a CSRF cookie"
    client.headers[CSRF_HEADER_NAME] = csrf_token
    return client
