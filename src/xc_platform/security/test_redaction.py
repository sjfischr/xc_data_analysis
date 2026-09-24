"""Unit tests for :mod:`xc_platform.security.redaction`.

All test inputs use dummy, non-functional credential-shaped values only. No
real secret is referenced. These tests validate the redaction guarantees for
headers, cookies, query parameters, and API-key-like values required by
Requirements 16.6 and 18.6.
"""

from __future__ import annotations

from xc_platform.security import redaction
from xc_platform.security.redaction import REDACTED

# --- Authorization headers -------------------------------------------------


def test_redact_headers_authorization_value_removed() -> None:
    headers = {
        "Authorization": "Bearer dummy-token-abc123",
        "Accept": "application/json",
    }
    result = redaction.redact_headers(headers)

    assert result["Authorization"] == REDACTED
    assert "dummy-token-abc123" not in str(result)
    # Non-sensitive headers are preserved.
    assert result["Accept"] == "application/json"


def test_redact_headers_is_case_insensitive() -> None:
    headers = {"authorization": "Basic ZHVtbXk6ZHVtbXk="}
    result = redaction.redact_headers(headers)
    assert result["authorization"] == REDACTED


def test_redact_string_inline_authorization_header() -> None:
    line = "GET /api request headers Authorization: Bearer dummy-token-xyz done"
    result = redaction.redact_string(line)
    assert "dummy-token-xyz" not in result
    assert REDACTED in result
    # Field name preserved for diagnostics.
    assert "Authorization" in result


def test_redact_string_bearer_token_anywhere() -> None:
    line = "token was Bearer abc.def.ghi in the request"
    result = redaction.redact_string(line)
    assert "abc.def.ghi" not in result
    assert REDACTED in result


# --- Cookies ---------------------------------------------------------------


def test_redact_headers_cookie_and_set_cookie() -> None:
    headers = {
        "Cookie": "session=dummysession123; theme=dark",
        "Set-Cookie": "session=dummysession456; HttpOnly; Secure",
    }
    result = redaction.redact_headers(headers)
    assert result["Cookie"] == REDACTED
    assert result["Set-Cookie"] == REDACTED
    assert "dummysession123" not in str(result)
    assert "dummysession456" not in str(result)


def test_redact_string_inline_cookie_header() -> None:
    line = "Set-Cookie: session=dummysession789; Path=/"
    result = redaction.redact_string(line)
    assert "dummysession789" not in result
    assert REDACTED in result


# --- Query parameters ------------------------------------------------------


def test_redact_url_api_key_query_param() -> None:
    url = "https://example.com/results?api_key=dummykey12345&page=2"
    result = redaction.redact_url(url)
    assert "dummykey12345" not in result
    assert "api_key=" + REDACTED in result
    # Non-sensitive params preserved.
    assert "page=2" in result


def test_redact_url_token_query_param() -> None:
    url = "https://example.com/x?token=dummytoken999#frag"
    result = redaction.redact_url(url)
    assert "dummytoken999" not in result
    assert REDACTED in result
    # Fragment boundary respected.
    assert "#frag" in result


def test_redact_string_multiple_query_params() -> None:
    line = "fetching https://api.test/v1?key=dummy1&token=dummy2&user=bob"
    result = redaction.redact_string(line)
    assert "dummy1" not in result
    assert "dummy2" not in result
    assert "user=bob" in result


# --- API-key-like values ---------------------------------------------------


def test_redact_string_prefixed_api_key() -> None:
    line = "using key sk-abcdefgh12345678 for the client"
    result = redaction.redact_string(line)
    assert "sk-abcdefgh12345678" not in result
    assert REDACTED in result


def test_redact_string_tavily_style_key() -> None:
    line = "TAVILY key tvly-dummyKEY1234567890 loaded"
    result = redaction.redact_string(line)
    assert "tvly-dummyKEY1234567890" not in result
    assert REDACTED in result


def test_redact_string_aws_access_key_id() -> None:
    line = "credentials AKIAIOSFODNN7EXAMPLE in log"
    result = redaction.redact_string(line)
    assert "AKIAIOSFODNN7EXAMPLE" not in result
    assert REDACTED in result


# --- Structured mappings ---------------------------------------------------


def test_redact_mapping_sensitive_keys() -> None:
    data = {
        "username": "alice",
        "password": "dummy-pass",
        "api_key": "dummy-key",
        "nested": {"session_token": "dummy-session", "count": 3},
    }
    result = redaction.redact_mapping(data)
    assert result["username"] == "alice"
    assert result["password"] == REDACTED
    assert result["api_key"] == REDACTED
    assert result["nested"]["session_token"] == REDACTED
    assert result["nested"]["count"] == 3


def test_redact_mapping_scrubs_string_values() -> None:
    data = {"log_line": "Authorization: Bearer dummy-inline-token"}
    result = redaction.redact_mapping(data)
    assert "dummy-inline-token" not in result["log_line"]
    assert REDACTED in result["log_line"]


def test_redact_value_handles_sequences() -> None:
    data = {"lines": ["Authorization: Bearer dummytok1", "plain text"]}
    result = redaction.redact_mapping(data)
    assert "dummytok1" not in str(result)
    assert result["lines"][1] == "plain text"


def test_redaction_does_not_mutate_input() -> None:
    original = {"password": "dummy-pass", "keep": "value"}
    redaction.redact_mapping(original)
    # Original is untouched.
    assert original["password"] == "dummy-pass"


def test_redact_string_leaves_ordinary_text_untouched() -> None:
    line = "Race 154050 results page 2 for varsity boys 2024"
    assert redaction.redact_string(line) == line
