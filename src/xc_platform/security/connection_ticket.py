"""Short-lived WebSocket connection tickets (Task 13.5, design.md section
6.2/12.4), promoted to production from the Task 3.7 feasibility gate
(``src/xc_platform/feasibility/connection_ticket.py``, docs/auth-delivery-
evidence.md: "WebSocket connection-ticket validation -- Proven (logic; not
deployed)").

Browsers cannot set custom headers on a WebSocket handshake, so the naive
approach is to put the session token in the query string -- which leaks it
into CloudFront logs, API Gateway access logs, and browser history
(Requirement 16.6). Instead the authenticated HTTP API mints a **ticket**: a
short-lived, single-use, HMAC-signed value bound to one session, actor, and
role, supplied as a WebSocket subprotocol instead of a URL parameter.

Properties enforced here (identical to the validated feasibility logic):

* signed with HMAC-SHA256 over every field, so nothing is mutable in transit;
* short TTL (default 60s) -- a leaked ticket is useless almost immediately;
* single-use -- replay is rejected even inside the TTL;
* bound to session id, actor id, and role, so a viewer ticket cannot be
  replayed on an administrator session;
* constant-time comparison, and no secret material in the ticket body.

The one production change from the feasibility version: the single-use
"seen" store is a :class:`TicketReplayStore` Protocol rather than a bare
``set`` -- an in-process set (:class:`InMemoryTicketReplayStore`) is correct
for local/test use only; the feasibility gate's own evidence already flags
that production needs a short-TTL shared store (DynamoDB or ElastiCache,
Task 15.2) or replay protection silently fails across separate instances.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_TTL_SECONDS = 60


class TicketError(RuntimeError):
    """Raised when a ticket is malformed, expired, replayed, or forged."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded)


def issue_ticket(
    *,
    session_id: str,
    actor_id: str,
    role: str,
    signing_key: bytes,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Mint a signed, short-lived ticket for one WebSocket connection."""
    if role not in {"admin", "viewer"}:
        raise TicketError(f"unsupported role: {role!r}")
    issued_at = now if now is not None else time.time()
    payload = {
        "sid": session_id,
        "aid": actor_id,
        "role": role,
        "iat": int(issued_at),
        "exp": int(issued_at + ttl_seconds),
        "jti": secrets.token_urlsafe(12),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(signing_key, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


class TicketReplayStore(Protocol):
    def has_seen(self, jti: str) -> bool: ...

    def mark_seen(self, jti: str) -> None: ...


@dataclass
class InMemoryTicketReplayStore:
    """Local/test implementation only -- see the module docstring."""

    _seen: set[str] = field(default_factory=set)

    def has_seen(self, jti: str) -> bool:
        return jti in self._seen

    def mark_seen(self, jti: str) -> None:
        self._seen.add(jti)


@dataclass
class TicketValidator:
    signing_key: bytes
    replay_store: TicketReplayStore

    def validate(
        self,
        ticket: str,
        *,
        expected_session: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = now if now is not None else time.time()
        try:
            body, signature = ticket.split(".", 1)
        except ValueError as error:
            raise TicketError("malformed ticket") from error

        expected_signature = hmac.new(
            self.signing_key, body.encode("ascii"), hashlib.sha256
        ).digest()
        try:
            supplied_signature = _b64decode(signature)
        except (ValueError, TypeError) as error:
            raise TicketError("malformed signature") from error
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise TicketError("signature mismatch")

        try:
            payload: dict[str, Any] = json.loads(_b64decode(body))
        except (ValueError, TypeError) as error:
            raise TicketError("malformed payload") from error

        if int(payload.get("exp", 0)) <= current:
            raise TicketError("ticket expired")
        if expected_session is not None and payload.get("sid") != expected_session:
            raise TicketError("ticket is not bound to this session")

        jti = str(payload.get("jti", ""))
        if not jti:
            raise TicketError("ticket has no unique id")
        if self.replay_store.has_seen(jti):
            raise TicketError("ticket already used")
        self.replay_store.mark_seen(jti)
        return payload
