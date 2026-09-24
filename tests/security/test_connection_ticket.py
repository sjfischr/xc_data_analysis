"""Security tests for the Task 3.7 WebSocket connection ticket (gate F10)."""

from __future__ import annotations

import time

import pytest

from xc_platform.feasibility.connection_ticket import (
    TicketError,
    TicketValidator,
    actor_id_for,
    issue_ticket,
)

KEY = b"feasibility-signing-key-not-a-real-secret"
OTHER_KEY = b"a-different-key-entirely-for-testing-only"


def make_validator() -> TicketValidator:
    return TicketValidator(signing_key=KEY, seen=set())


def test_valid_ticket_round_trips() -> None:
    ticket = issue_ticket(
        session_id="sess-1", actor_id="actor-1", role="viewer", signing_key=KEY
    )
    payload = make_validator().validate(ticket, expected_session="sess-1")
    assert payload["sid"] == "sess-1"
    assert payload["role"] == "viewer"


def test_ticket_is_single_use() -> None:
    """Replay must fail even well inside the TTL."""
    validator = make_validator()
    ticket = issue_ticket(
        session_id="sess-1", actor_id="actor-1", role="admin", signing_key=KEY
    )
    validator.validate(ticket)
    with pytest.raises(TicketError, match="already used"):
        validator.validate(ticket)


def test_expired_ticket_is_rejected() -> None:
    past = time.time() - 3600
    ticket = issue_ticket(
        session_id="sess-1",
        actor_id="actor-1",
        role="viewer",
        signing_key=KEY,
        ttl_seconds=60,
        now=past,
    )
    with pytest.raises(TicketError, match="expired"):
        make_validator().validate(ticket)


def test_ticket_signed_with_another_key_is_rejected() -> None:
    forged = issue_ticket(
        session_id="sess-1", actor_id="actor-1", role="admin", signing_key=OTHER_KEY
    )
    with pytest.raises(TicketError, match="signature mismatch"):
        make_validator().validate(forged)


def test_tampered_payload_is_rejected() -> None:
    """Escalating viewer to admin by editing the body must break the signature."""
    ticket = issue_ticket(
        session_id="sess-1", actor_id="actor-1", role="viewer", signing_key=KEY
    )
    body, signature = ticket.split(".", 1)
    import base64
    import json

    padded = body + "=" * (-len(body) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    payload["role"] = "admin"
    tampered_body = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    with pytest.raises(TicketError, match="signature mismatch"):
        make_validator().validate(f"{tampered_body}.{signature}")


def test_ticket_cannot_be_replayed_on_another_session() -> None:
    ticket = issue_ticket(
        session_id="sess-1", actor_id="actor-1", role="admin", signing_key=KEY
    )
    with pytest.raises(TicketError, match="not bound to this session"):
        make_validator().validate(ticket, expected_session="sess-2")


@pytest.mark.parametrize(
    "ticket", ["", "nodot", "a.b.c.d", "!!!.???", "eyJhIjoxfQ"]
)
def test_malformed_tickets_are_rejected(ticket: str) -> None:
    with pytest.raises(TicketError):
        make_validator().validate(ticket)


def test_unsupported_role_cannot_be_issued() -> None:
    with pytest.raises(TicketError, match="unsupported role"):
        issue_ticket(
            session_id="s", actor_id="a", role="superuser", signing_key=KEY
        )


def test_actor_id_is_pseudonymous_and_stable() -> None:
    """The Cognito subject must not be recoverable from the actor id."""
    subject = "8f14e45f-ea4b-4a2c-9c1f-000000000000"
    first = actor_id_for(subject, KEY)
    second = actor_id_for(subject, KEY)
    assert first == second, "actor id must be stable for the same user"
    assert subject not in first
    assert "@" not in first
    assert actor_id_for(subject, OTHER_KEY) != first


def test_different_users_get_different_actor_ids() -> None:
    assert actor_id_for("user-a", KEY) != actor_id_for("user-b", KEY)
