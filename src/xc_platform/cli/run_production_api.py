"""Production entrypoint for the FastAPI API (Task 18.1). Runs inside the
container `Dockerfile` builds, deployed to App Runner by
``infrastructure/cdk/stacks/api_stack.py``.

Distinct from :mod:`xc_platform.cli.run_local_api`, which is explicitly
local-dev-only (in-memory S3, a throwaway local SQLite file). This module
uses the real ``Boto3S3Client`` against a real bucket and reads secrets
from SSM SecureString (`security.config.get_secret`, Task 18.1's other new
piece).

**Known, accepted limitation for this first cutover** (recorded here and
in ``docs/operations-and-acceptance-readiness.md``, not glossed over):
``SessionStore``/``TicketReplayStore`` are the in-memory implementations.
Correct for exactly one running instance -- this service is deployed with
``min_size=max_size=1`` for that reason, matching the SQLite single-writer
constraint the rest of this design already assumes -- but a restart or
redeploy invalidates every session (users re-login; no data is lost) and
this does not scale past one instance without swapping in a DynamoDB-backed
store behind the same ``SessionStore``/``TicketReplayStore`` Protocols
(already the documented seam in ``security/session.py``/
``connection_ticket.py``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from xc_platform.agents.code_interpreter import AgentCoreCodeInterpreterExecutor
from xc_platform.api.agent_invoker import (
    AgentCoreRuntimeInvoker,
    AgentInvoker,
    AgentSwitch,
    InProcessAgentInvoker,
)
from xc_platform.api.app import create_app
from xc_platform.api.context import (
    AppContext,
    CognitoOAuthConfig,
    RunSignupClientFactory,
)
from xc_platform.db.migrator import latest_schema_version
from xc_platform.db.publication.hydration import hydrate_writer_database
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import Boto3S3Client
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.ingest.adapters.runsignup_client import RequestBudget, RunSignupClient
from xc_platform.ingest.raw_storage import RawObjectStore
from xc_platform.security.config import get_secret
from xc_platform.security.connection_ticket import InMemoryTicketReplayStore
from xc_platform.security.session import InMemorySessionStore


class _LiveRunSignupClientFactory(RunSignupClientFactory):
    def __call__(self) -> RunSignupClient:
        return RunSignupClient(
            budget=RequestBudget(max_requests=200, max_wall_time_s=120.0)
        )


def _build_cognito_oauth_config(region: str | None) -> CognitoOAuthConfig | None:
    # Domain/user pool ID come straight from App Runner's own env vars
    # (CDK-injected, no circular dependency -- see web_stack.py). Client ID
    # deliberately does not: it is SSM-sourced, an operator sets it once
    # after the app client exists (same one-time step as the ticket-signing
    # secrets). Until that happens, this returns None and the OAuth routes
    # 503 cleanly rather than crash-looping the whole API over one
    # not-yet-configured feature.
    domain = os.environ.get("XC_COGNITO_DOMAIN")
    user_pool_id = os.environ.get("XC_COGNITO_USER_POOL_ID")
    client_id = get_secret("XC_COGNITO_CLIENT_ID", default="")
    if not (domain and user_pool_id and client_id and region):
        return None
    return CognitoOAuthConfig(
        domain=domain, client_id=client_id, user_pool_id=user_pool_id, region=region
    )


def _build_agent_invoker(
    snapshot_reader: SnapshotReader, region: str | None
) -> AgentInvoker | None:
    """Task 19.2. AgentCore when its runtime ARN is configured (the
    deployed path); otherwise the in-process agent, which needs the
    instance role's own Bedrock grant and exists as a fallback."""
    runtime_arn = os.environ.get("XC_AGENT_RUNTIME_ARN")
    resolved_region = region or "us-east-1"
    if runtime_arn:
        return AgentCoreRuntimeInvoker(runtime_arn=runtime_arn, region=resolved_region)
    interpreter_id = os.environ.get("XC_CODE_INTERPRETER_ID")
    return InProcessAgentInvoker(
        snapshot_reader=snapshot_reader,
        python_executor_factory=(
            (
                lambda: AgentCoreCodeInterpreterExecutor(
                    region=resolved_region, interpreter_id=interpreter_id
                )
            )
            if interpreter_id
            else None
        ),
    )


def build_production_context(*, data_dir: Path) -> AppContext:
    bucket = os.environ["XC_SNAPSHOT_BUCKET"]
    region = os.environ.get("AWS_REGION")
    s3 = Boto3S3Client.create(bucket=bucket, region=region)

    # Fails fast and loudly at startup instead of a confusing 500 on the
    # first real read: found live during Task 18.2's validation
    # (2026-09-23), a Dockerfile missing `COPY migrations ./migrations`
    # made the container find zero migration files, so it silently applied
    # none to its own local database *and* rejected every real manifest as
    # "newer than supported". This app always ships with real migrations,
    # so zero can only mean the migrations directory failed to reach the
    # image.
    if latest_schema_version() == 0:
        raise RuntimeError(
            "No migration files found -- the migrations/ directory did not "
            "reach this container (check the Dockerfile's COPY steps)."
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "xc.db"
    snapshot_reader = SnapshotReader(s3, cache_dir=data_dir / "cache")
    # Task 19.0: the writer starts from the live published generation, not
    # an empty database -- otherwise every commit publishes as a bootstrap
    # and the publisher's compare-and-swap refuses it.
    conn, _ = hydrate_writer_database(snapshot_reader, db_path)

    return AppContext(
        db_path=db_path,
        writer_conn=conn,
        snapshot_reader=snapshot_reader,
        snapshot_publisher=SnapshotPublisher(
            s3, work_dir=data_dir / "pub-work", owner_id="production-api"
        ),
        raw_store=RawObjectStore(s3, work_dir=data_dir / "raw-work"),
        runsignup_client_factory=_LiveRunSignupClientFactory(),
        session_store=InMemorySessionStore(),
        ticket_signing_key=get_secret("XC_TICKET_SIGNING_KEY").encode("utf-8"),
        ticket_replay_store=InMemoryTicketReplayStore(),
        actor_id_application_key=get_secret("XC_ACTOR_ID_APPLICATION_KEY").encode(
            "utf-8"
        ),
        cognito_oauth=_build_cognito_oauth_config(region),
        frontend_origin=os.environ.get("XC_FRONTEND_ORIGIN"),
        agent_invoker=_build_agent_invoker(snapshot_reader, region),
        agent_switch=AgentSwitch(),
    )


def main(argv: list[str] | None = None) -> int:
    ctx = build_production_context(data_dir=Path("/tmp/xc-data"))  # noqa: S108 -- container-local scratch, never authoritative
    frontend_origin = ctx.frontend_origin
    app = create_app(
        ctx,
        cors_allowed_origins=(frontend_origin,) if frontend_origin else (),
        include_dev_login=False,
    )
    port = int(os.environ.get("PORT", "8080"))
    print(f"Production API starting on 0.0.0.0:{port}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port)  # noqa: S104 -- container must accept App Runner's health/traffic probes from outside localhost
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
