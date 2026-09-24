from __future__ import annotations

from xc_platform.security.markdown_sanitizer import sanitize_markdown


def test_a_script_tag_is_escaped_not_executable() -> None:
    sanitized = sanitize_markdown("Here you go <script>alert(document.cookie)</script>")
    assert "<script>" not in sanitized
    assert "&lt;script&gt;" in sanitized


def test_an_event_handler_attribute_is_escaped() -> None:
    sanitized = sanitize_markdown('<img src=x onerror="alert(1)">')
    assert "<img" not in sanitized
    assert "&lt;img" in sanitized


def test_a_javascript_uri_scheme_is_blocked() -> None:
    sanitized = sanitize_markdown("[click me](javascript:alert(1))")
    assert "javascript:" not in sanitized
    assert "blocked:" in sanitized


def test_a_data_uri_scheme_is_blocked() -> None:
    sanitized = sanitize_markdown("[open](data:text/html,<script>alert(1)</script>)")
    assert "data:" not in sanitized


def test_ordinary_markdown_content_is_preserved_in_spirit() -> None:
    text = "Jane Doe improved her pace by 12s. See **the trend**."
    sanitized = sanitize_markdown(text)
    assert "Jane Doe improved her pace by 12s." in sanitized
    assert "**the trend**" in sanitized


def test_double_sanitizing_never_reintroduces_a_live_tag() -> None:
    """Not true idempotency (re-escaping already-escaped text double-
    escapes '&', which is expected) -- the actual safety property is that
    sanitizing twice still never produces a live '<script>' or
    'javascript:' scheme, which is what matters if a caller ever sanitizes
    the same text more than once by mistake."""
    text = "<script>bad()</script> and a [link](javascript:evil())"
    once = sanitize_markdown(text)
    twice = sanitize_markdown(once)
    assert "<script>" not in twice
    assert "javascript:" not in twice
