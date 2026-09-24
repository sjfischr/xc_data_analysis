"""Ports the Task 3.7 feasibility gate's 14 connection-ticket security
checks (docs/auth-delivery-evidence.md) onto the production module."""

from __future__ import annotations

import pytest

from xc_platform.security.connection_ticket import (
    InMemoryTicketReplayStore,
    TicketError,
    TicketValidator,
    issue_ticket,
)

KEY = b"test-signing-key"
OTHER_KEY = b"a-completely-different-key"


def _validator() -> TicketValidator:
    return TicketValidator(signing_key=KEY, replay_store=InMemoryTicketReplayStore())


def test_a_valid_ticket_round_trips() -> None:
    ticket = issue_ticket(
        session_id="s1", actor_id="a1", role="viewer", signing_key=KEY
    )
    payload = _validator().validate(ticket)
    assert payload["sid"] == "s1"
    assert payload["aid"] == "a1"
    assert payload["role"] == "viewer"


def test_replay_is_rejected_even_within_the_ttl() -> None:
    ticket = issue_ticket(
        session_id="s1", actor_id="a1", role="viewer", signing_key=KEY
    )
    validator = _validator()
    validator.validate(ticket)
    with pytest.raises(TicketError, match="already used"):
        validator.validate(ticket)


def test_an_expired_ticket_is_rejected() -> None:
    ticket = issue_ticket(
        session_id="s1",
        actor_id="a1",
        role="viewer",
        signing_key=KEY,
        ttl_seconds=1,
        now=1000.0,
    )
    with pytest.raises(TicketError, match="expired"):
        _validator().validate(ticket, now=1002.0)


def test_a_ticket_signed_with_a_different_key_is_rejected() -> None:
    ticket = issue_ticket(
        session_id="s1", actor_id="a1", role="viewer", signing_key=OTHER_KEY
    )
    with pytest.raises(TicketError, match="signature mismatch"):
        _validator().validate(ticket)


def test_a_tampered_payload_escalating_role_is_rejected() -> None:
    import base64
    import json

    ticket = issue_ticket(
        session_id="s1", actor_id="a1", role="viewer", signing_key=KEY
    )
    body, signature = ticket.split(".", 1)
    padded = body + "=" * (-len(body) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    payload["role"] = "admin"
    tampered_body = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode("ascii")
        .rstrip("=")
    )
    tampered_ticket = f"{tampered_body}.{signature}"
    with pytest.raises(TicketError, match="signature mismatch"):
        _validator().validate(tampered_ticket)


def test_a_ticket_replayed_on_a_different_session_is_rejected() -> None:
    ticket = issue_ticket(
        session_id="s1", actor_id="a1", role="viewer", signing_key=KEY
    )
    with pytest.raises(TicketError, match="not bound to this session"):
        _validator().validate(ticket, expected_session="s2")


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "no-dot-at-all",
        "onlyonepart.",
        ".onlysignature",
        "not-base64!!!.not-base64-either!!!",
    ],
)
def test_malformed_tickets_are_rejected(malformed: str) -> None:
    with pytest.raises(TicketError):
        _validator().validate(malformed)


def test_an_unsupported_role_cannot_be_issued() -> None:
    with pytest.raises(TicketError, match="unsupported role"):
        issue_ticket(session_id="s1", actor_id="a1", role="superadmin", signing_key=KEY)


def test_replay_store_is_pluggable_and_isolated_per_instance() -> None:
    """Two validators with separate replay stores don't interfere -- the
    property production needs when the store is swapped for a shared
    backend (the module docstring's documented consequence)."""
    ticket = issue_ticket(
        session_id="s1", actor_id="a1", role="viewer", signing_key=KEY
    )
    validator_a = _validator()
    validator_b = _validator()
    validator_a.validate(ticket)
    # A second, independent store has never seen this jti.
    payload = validator_b.validate(ticket)
    assert payload["sid"] == "s1"
