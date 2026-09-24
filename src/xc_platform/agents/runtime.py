"""Runtime-neutral local/test adapter for the analytics agent (Task 11.5,
design.md 12.2's "local CLI/test runtime" and "emergency in-process
development mode").

This is the portability the whole agent layer is designed around: the same
:func:`~xc_platform.agents.analytics_agent.build_analytics_agent` this
module calls is exactly what an AgentCore entrypoint
(:mod:`xc_platform.agents.agentcore_entrypoints`) also calls -- only how the
snapshot and model get resolved differs. This module resolves both locally
(:class:`~xc_platform.db.publication.reader.SnapshotReader` against a real
or fake S3 client, an injectable model), never through AgentCore.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from strands import Agent

from xc_platform.agents.analytics_agent import build_analytics_agent
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.repositories.canonical import CanonicalReadRepository


@dataclass(frozen=True, slots=True)
class AnalyticsSession:
    """One session's agent plus the exact publication it is pinned to for
    the session's lifetime (design.md 12.3: a session never silently
    re-resolves "current" mid-conversation)."""

    agent: Agent
    publication_id: str
    connection: sqlite3.Connection


@contextmanager
def analytics_session(
    snapshot_reader: SnapshotReader,
    *,
    model: Any | None = None,
    model_id: str | None = None,
    region_name: str | None = None,
) -> Iterator[AnalyticsSession]:
    """Resolve the current published snapshot, open it read-only, build an
    agent bound to it, and guarantee the connection closes afterward."""
    pinned = snapshot_reader.current()
    connection = pinned.open_connection()
    try:
        canonical = CanonicalReadRepository(connection)
        agent = build_analytics_agent(
            canonical,
            publication_id=pinned.manifest.publication_id,
            model=model,
            model_id=model_id,
            region_name=region_name,
        )
        yield AnalyticsSession(
            agent=agent,
            publication_id=pinned.manifest.publication_id,
            connection=connection,
        )
    finally:
        connection.close()
