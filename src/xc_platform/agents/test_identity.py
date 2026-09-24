from __future__ import annotations

import pytest

from xc_platform.agents.identity import derive_actor_id


def test_same_subject_and_key_produce_the_same_actor_id() -> None:
    key = b"app-secret"
    assert derive_actor_id("cognito-sub-1", key) == derive_actor_id(
        "cognito-sub-1", key
    )


def test_different_subjects_produce_different_actor_ids() -> None:
    key = b"app-secret"
    assert derive_actor_id("sub-a", key) != derive_actor_id("sub-b", key)


def test_different_keys_produce_different_actor_ids_for_the_same_subject() -> None:
    assert derive_actor_id("sub-a", b"key-1") != derive_actor_id("sub-a", b"key-2")


def test_actor_id_does_not_contain_the_raw_subject() -> None:
    actor_id = derive_actor_id("coach.jane@example.com", b"app-secret")
    assert "coach.jane" not in actor_id
    assert "example.com" not in actor_id


def test_empty_subject_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        derive_actor_id("", b"app-secret")
