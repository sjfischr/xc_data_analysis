"""The API app's runtime dependencies, bundled so tests can inject fakes
for every one of them (Task 13.1).

Nothing here is FastAPI-specific -- :mod:`xc_platform.api.app` wires this
into ``app.state`` once at startup and every route reads it back through a
plain dependency function, so route handlers never construct their own
database connections or clients.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xc_platform.api.agent_invoker import (
    AgentInvoker,
    AgentSwitch,
    InProcessAgentInvoker,
)
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.ingest.adapters.runsignup_client import RunSignupClient
from xc_platform.ingest.raw_storage import RawObjectStore
from xc_platform.security.connection_ticket import TicketReplayStore
from xc_platform.security.session import SessionStore


@dataclass(frozen=True, slots=True)
class CognitoOAuthConfig:
    """Task 18.2: real Cognito hosted-UI login. ``client_id`` is
    deliberately not read from a CDK-injected env var -- see
    ``infrastructure/cdk/stacks/web_stack.py``'s ``AppClient`` comment for
    why that would be a circular CloudFormation dependency; an operator
    sets it via SSM once, the same one-time step already used for the
    ticket-signing secrets."""

    domain: str
    client_id: str
    user_pool_id: str
    region: str


@dataclass
class AppContext:
    """One instance per running app (or per test). Owns the single writer
    connection (design.md section 7's single-writer constraint) and every
    client/store a route needs, all injectable."""

    db_path: Path
    writer_conn: sqlite3.Connection
    snapshot_reader: SnapshotReader
    snapshot_publisher: SnapshotPublisher
    raw_store: RawObjectStore
    runsignup_client_factory: RunSignupClientFactory
    session_store: SessionStore
    ticket_signing_key: bytes
    ticket_replay_store: TicketReplayStore
    actor_id_application_key: bytes
    # Tests inject a factory returning a scripted
    # xc_platform.agents._fake_model.FakeModel so the chat route's real
    # tool-calling loop runs without a live Bedrock call. Used only by the
    # default in-process invoker below.
    analytics_model_factory: Callable[[], Any] | None = None
    # Task 19.2: how chat reaches the agent. None = an in-process invoker
    # built lazily from this context (local dev, tests); production sets an
    # AgentCoreRuntimeInvoker when XC_AGENT_RUNTIME_ARN is configured.
    agent_invoker: AgentInvoker | None = None
    # Task 19.4: the global kill switch. None = always on (local, tests).
    agent_switch: AgentSwitch | None = None
    # None everywhere except run_production_api.py (Task 18.2) -- routes
    # that need it 503 with a clear message instead, rather than crashing,
    # so local dev/tests never need to fabricate Cognito config.
    cognito_oauth: CognitoOAuthConfig | None = None
    # None everywhere except run_production_api.py (Task 13/14 login fix):
    # when set, auth.py's callback() redirects here after a successful
    # login instead of returning a JSON body -- the real frontend's
    # landing page after Cognito's hosted UI sends the browser back.
    frontend_origin: str | None = None
    # Task 19.3: the single writer connection is shared by every request
    # thread; SQLite interleaving two requests' statements broke a review
    # decision halfway (found in local testing, 2026-09-24). Every route
    # that touches writer_conn holds this for the whole request
    # (deps.writer_access). A plain Lock, not RLock: FastAPI may run a
    # dependency's setup and teardown on different worker threads.
    writer_lock: threading.Lock = field(default_factory=threading.Lock)

    def get_agent_invoker(self) -> AgentInvoker:
        if self.agent_invoker is None:
            self.agent_invoker = InProcessAgentInvoker(
                snapshot_reader=self.snapshot_reader,
                model_factory=self.analytics_model_factory,
            )
        return self.agent_invoker

    @property
    def staging(self) -> StagingRepository:
        return StagingRepository(self.writer_conn)

    @property
    def ingest(self) -> IngestRunRepository:
        return IngestRunRepository(self.writer_conn)

    @property
    def resolver(self) -> ResolutionRepository:
        return ResolutionRepository(self.writer_conn)


class RunSignupClientFactory:
    """A callable that returns a fresh :class:`RunSignupClient` per request
    -- request budgets are per-invocation (Requirement 5.6), never shared
    or reused across requests."""

    def __call__(self) -> RunSignupClient:  # pragma: no cover - overridden in tests
        raise NotImplementedError
