"""Server-side session store for the cookie-session (BFF) authentication
pattern (Task 13.4, design.md section 6.2).

Confirmed live, Task 3.7 (docs/protected-api-evidence.md): a stateless JWT
authorizer never sees Cognito revocation -- a globally-signed-out token
stayed valid against it until natural expiry. This module is the fix: the
API reads a session identifier from a Secure/HttpOnly/SameSite cookie and
checks it against a server-side session record on **every** request.
Revoking a session (:meth:`SessionStore.revoke`) takes effect on the very
next request, not at token expiry -- the property a bare bearer-JWT pattern
cannot deliver. Do not substitute a bare bearer-JWT-only pattern for this
(tasks.md Task 13.4's explicit instruction, carrying the same finding).

:class:`SessionStore` is a Protocol so the same session-checking code runs
against :class:`InMemorySessionStore` locally/in tests and a real
persistent store (DynamoDB, Task 15.2) in production, without this module
or the FastAPI dependencies that use it needing to change.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60  # 8 hours; configurable per Requirement 13.7
SESSION_COOKIE_NAME = "xc_session"
CSRF_COOKIE_NAME = "xc_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"


class SessionError(RuntimeError):
    """The session is missing, expired, or has been revoked."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    actor_id: str
    role: str
    csrf_token: str
    created_at: float
    expires_at: float
    revoked: bool = False
    # Task 19.4: per-user access to the analytics agent (Cognito group
    # `agent-users`; admins always), fixed at login like the role.
    agent_access: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.revoked and time.time() < self.expires_at


class SessionStore(Protocol):
    def create(
        self,
        *,
        actor_id: str,
        role: str,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        agent_access: bool = False,
    ) -> SessionRecord: ...

    def get(self, session_id: str) -> SessionRecord | None: ...

    def revoke(self, session_id: str) -> None: ...

    def revoke_all_for_actor(self, actor_id: str) -> int: ...


@dataclass
class InMemorySessionStore:
    """Local/test implementation. Production (Task 15.2) swaps in a
    DynamoDB-backed store behind the same :class:`SessionStore` Protocol --
    the in-process dict here does not survive a restart or scale across
    instances, which is fine for a single local process or a test, never
    for production (same "carried design consequence" the connection-
    ticket replay store already documents)."""

    _sessions: dict[str, SessionRecord] = field(default_factory=dict)

    def create(
        self,
        *,
        actor_id: str,
        role: str,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        agent_access: bool = False,
    ) -> SessionRecord:
        if role not in {"admin", "viewer"}:
            raise ValueError(f"unsupported role: {role!r}")
        now = time.time()
        record = SessionRecord(
            session_id=secrets.token_urlsafe(32),
            actor_id=actor_id,
            role=role,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + ttl_seconds,
            agent_access=agent_access or role == "admin",
        )
        self._sessions[record.session_id] = record
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def revoke(self, session_id: str) -> None:
        existing = self._sessions.get(session_id)
        if existing is not None:
            self._sessions[session_id] = SessionRecord(
                session_id=existing.session_id,
                actor_id=existing.actor_id,
                role=existing.role,
                csrf_token=existing.csrf_token,
                created_at=existing.created_at,
                expires_at=existing.expires_at,
                revoked=True,
                agent_access=existing.agent_access,
            )

    def revoke_all_for_actor(self, actor_id: str) -> int:
        """Global sign-out for one actor (Requirement 13.7's revocation) --
        every session this actor holds, not just the calling one."""
        count = 0
        for session_id, record in list(self._sessions.items()):
            if record.actor_id == actor_id and not record.revoked:
                self.revoke(session_id)
                count += 1
        return count


def require_valid_session(store: SessionStore, session_id: str | None) -> SessionRecord:
    """Raise :class:`SessionError` unless ``session_id`` names a live,
    unexpired, unrevoked session. The single check every protected route
    goes through (Requirement 13.3: enforced on the server, not left to a
    hidden UI element)."""
    if not session_id:
        raise SessionError("no session cookie presented")
    record = store.get(session_id)
    if record is None:
        raise SessionError("unknown session")
    if not record.is_valid:
        raise SessionError("session expired or revoked")
    return record
