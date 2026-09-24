from __future__ import annotations

import time

import pytest

from xc_platform.security.session import (
    InMemorySessionStore,
    SessionError,
    require_valid_session,
)


def test_create_returns_a_valid_session() -> None:
    store = InMemorySessionStore()
    record = store.create(actor_id="actor-1", role="viewer")
    assert record.is_valid
    assert store.get(record.session_id) == record


def test_unsupported_role_is_rejected() -> None:
    store = InMemorySessionStore()
    with pytest.raises(ValueError, match="unsupported role"):
        store.create(actor_id="actor-1", role="superuser")


def test_require_valid_session_accepts_a_live_session() -> None:
    store = InMemorySessionStore()
    record = store.create(actor_id="actor-1", role="admin")
    resolved = require_valid_session(store, record.session_id)
    assert resolved.actor_id == "actor-1"
    assert resolved.role == "admin"


def test_require_valid_session_rejects_a_missing_cookie() -> None:
    store = InMemorySessionStore()
    with pytest.raises(SessionError, match="no session cookie"):
        require_valid_session(store, None)


def test_require_valid_session_rejects_an_unknown_session_id() -> None:
    store = InMemorySessionStore()
    with pytest.raises(SessionError, match="unknown session"):
        require_valid_session(store, "does-not-exist")


def test_revoke_takes_effect_immediately_not_at_expiry() -> None:
    """Directly proves the Task 3.7 finding this module exists to fix: a
    revoked session is rejected on the very next check, unlike a stateless
    JWT authorizer which accepted a revoked-but-unexpired token."""
    store = InMemorySessionStore()
    record = store.create(actor_id="actor-1", role="viewer")
    assert (
        require_valid_session(store, record.session_id).session_id == record.session_id
    )

    store.revoke(record.session_id)

    with pytest.raises(SessionError, match="expired or revoked"):
        require_valid_session(store, record.session_id)


def test_revoke_all_for_actor_revokes_every_session_that_actor_holds() -> None:
    store = InMemorySessionStore()
    session_a = store.create(actor_id="actor-1", role="viewer")
    session_b = store.create(actor_id="actor-1", role="viewer")
    other_actor_session = store.create(actor_id="actor-2", role="viewer")

    revoked_count = store.revoke_all_for_actor("actor-1")

    assert revoked_count == 2
    with pytest.raises(SessionError):
        require_valid_session(store, session_a.session_id)
    with pytest.raises(SessionError):
        require_valid_session(store, session_b.session_id)
    # Unaffected: a different actor's session survives.
    assert require_valid_session(store, other_actor_session.session_id) is not None


def test_expired_session_is_rejected_even_without_explicit_revoke() -> None:
    store = InMemorySessionStore()
    record = store.create(actor_id="actor-1", role="viewer", ttl_seconds=0)
    time.sleep(0.01)
    with pytest.raises(SessionError, match="expired or revoked"):
        require_valid_session(store, record.session_id)


def test_each_session_gets_a_distinct_unguessable_id_and_csrf_token() -> None:
    store = InMemorySessionStore()
    a = store.create(actor_id="actor-1", role="viewer")
    b = store.create(actor_id="actor-1", role="viewer")
    assert a.session_id != b.session_id
    assert a.csrf_token != b.csrf_token
    assert len(a.session_id) >= 32
