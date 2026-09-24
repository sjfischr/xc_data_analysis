"""Run the API locally for frontend development (Task 14.1's dev loop).

Not a deployment path -- production runs behind CloudFront + API Gateway +
Lambda (design.md 15.2), never this script. This exists so
``pnpm dev`` in ``web/`` has a real backend to talk to on
``http://localhost:8000`` while building the frontend, with an in-memory
S3 (fine for one local process) and a fresh local SQLite database.

Usage::

    python -m xc_platform.cli.run_local_api
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

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
from xc_platform.security.session import InMemorySessionStore

DEFAULT_DATA_DIR = Path(".local")


class _LiveRunSignupClientFactory(RunSignupClientFactory):
    def __call__(self) -> RunSignupClient:
        return RunSignupClient(
            budget=RequestBudget(max_requests=200, max_wall_time_s=120.0)
        )


def build_local_context(data_dir: Path = DEFAULT_DATA_DIR) -> AppContext:
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "xc.db"
    conn = open_writer_connection(db_path)
    migrate(conn)

    s3 = FakeS3Client()
    publisher = SnapshotPublisher(
        s3, work_dir=data_dir / "pub-work", owner_id="local-dev"
    )
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    manifest = publisher.publish(
        db_path, created_by="local-dev", ingest_run_id="seed", summary={}
    )
    PublicationRepository(conn).record_publication(
        publication_id=manifest.publication_id,
        snapshot_key=manifest.snapshot_key,
        snapshot_version_id=manifest.snapshot_version_id,
        content_sha256=manifest.sha256,
        byte_size=manifest.byte_size,
        schema_version=manifest.schema_version,
        summary_json="{}",
    )

    return AppContext(
        db_path=db_path,
        writer_conn=conn,
        snapshot_reader=SnapshotReader(s3, cache_dir=data_dir / "cache"),
        snapshot_publisher=publisher,
        raw_store=RawObjectStore(s3, work_dir=data_dir / "raw-work"),
        runsignup_client_factory=_LiveRunSignupClientFactory(),
        session_store=InMemorySessionStore(),
        ticket_signing_key=b"local-dev-ticket-key-not-for-production",
        ticket_replay_store=InMemoryTicketReplayStore(),
        actor_id_application_key=b"local-dev-actor-key-not-for-production",
    )


def main(argv: list[str] | None = None) -> int:
    ctx = build_local_context()
    app = create_app(ctx, cors_allowed_origins=("http://localhost:3000",))
    print(
        f"Local API up: http://localhost:8000  (data: {ctx.db_path})", file=sys.stderr
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
