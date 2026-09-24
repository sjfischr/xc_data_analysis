from __future__ import annotations

from xc_platform.agents.memory_policy import (
    MAX_LONG_TERM_MEMORY_LENGTH,
    sanitize_for_long_term_memory,
)


def test_redacts_an_embedded_secret() -> None:
    # Built from parts so secret scanners don't flag this deliberately fake
    # value.
    fake_key = "sk-" + "abcdef123456"
    text = f"User prefers metric units. api_key={fake_key} was mentioned."
    sanitized = sanitize_for_long_term_memory(text)
    assert fake_key not in sanitized
    assert "prefers metric units" in sanitized


def test_redacts_an_email_address() -> None:
    sanitized = sanitize_for_long_term_memory(
        "Contact is coach.jane@example.com for updates."
    )
    assert "coach.jane@example.com" not in sanitized


def test_redacts_a_phone_number() -> None:
    sanitized = sanitize_for_long_term_memory("Call 555-123-4567 for details.")
    assert "555-123-4567" not in sanitized


def test_redacts_a_date_of_birth_field() -> None:
    sanitized = sanitize_for_long_term_memory(
        "Notes: date of birth: 2014-05-01. Likes pace charts."
    )
    assert "2014-05-01" not in sanitized
    assert "Likes pace charts" in sanitized


def test_redacts_a_street_address_field() -> None:
    sanitized = sanitize_for_long_term_memory(
        "home address: 123 Main St, Springfield. Prefers dark mode."
    )
    assert "123 Main St" not in sanitized
    assert "Prefers dark mode" in sanitized


def test_ordinary_preference_text_passes_through_unchanged() -> None:
    text = "Prefers pace charts in min/mile and default season 2026."
    assert sanitize_for_long_term_memory(text) == text


def test_truncates_after_redaction_not_before() -> None:
    long_text = "Prefers pace charts in min per mile. " * 100
    assert len(long_text) > MAX_LONG_TERM_MEMORY_LENGTH
    sanitized = sanitize_for_long_term_memory(long_text)
    assert sanitized.endswith("…[truncated]")
    assert len(sanitized) <= MAX_LONG_TERM_MEMORY_LENGTH + len("…[truncated]")
