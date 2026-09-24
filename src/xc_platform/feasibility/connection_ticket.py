"""Task 3.7 -- short-lived WebSocket connection tickets (gate F10).

Browsers cannot set custom headers on a WebSocket handshake, so the naive
approach is to put the session token in the query string. That leaks it into
CloudFront logs, API Gateway access logs, and browser history.

Instead the authenticated HTTP API mints a **ticket**: a short-lived,
single-use, HMAC-signed value bound to one session and actor, supplied as a
WebSocket subprotocol. This module is the reference implementation the
prototype validates.

Properties enforced here:

* signed with HMAC-SHA256 over every field, so nothing is mutable in transit;
* short TTL (default 60 s) -- a leaked ticket is useless almost immediately;
* single-use -- replay is rejected even inside the TTL;
* bound to session id, actor id, and role, so a viewer ticket cannot be
  replayed on an administrator session;
* constant-time comparison, and no secret material in the ticket body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_TTL_SECONDS = 60


class TicketError(RuntimeError):
    """Raised when a ticket is malformed, expired, replayed, or forged."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded)


def actor_id_for(user_sub: str, application_key: bytes) -> str:
    """Derive a pseudonymous actor id from the Cognito subject (design §12.4).

    The Cognito `sub` never reaches AgentCore Memory directly, and no email
    address is used in the namespace.
    """
    return hmac.new(
        application_key, user_sub.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


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


@dataclass
class TicketValidator:
    """Validates tickets and enforces single use.

    ``seen`` is in-process here for the prototype. In production this is a
    short-TTL store (DynamoDB or ElastiCache) so replay protection holds across
    Lambda instances -- recorded as a design consequence, not glossed over.
    """

    signing_key: bytes
    seen: set[str]

    def validate(
        self, ticket: str, *, expected_session: str | None = None, now: float | None = None
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
            payload = json.loads(_b64decode(body))
        except (ValueError, TypeError) as error:
            raise TicketError("malformed payload") from error

        if int(payload.get("exp", 0)) <= current:
            raise TicketError("ticket expired")
        if expected_session is not None and payload.get("sid") != expected_session:
            raise TicketError("ticket is not bound to this session")

        jti = str(payload.get("jti", ""))
        if not jti:
            raise TicketError("ticket has no unique id")
        if jti in self.seen:
            raise TicketError("ticket already used")
        self.seen.add(jti)
        return payload
