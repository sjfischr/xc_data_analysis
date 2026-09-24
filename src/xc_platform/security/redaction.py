"""Log-redaction utilities for the XC Data Platform.

These helpers strip credentials and other sensitive values out of strings and
structured records *before* they reach logs, traces, or diagnostic events. They
implement the redaction guarantees required by:

* Requirement 16.6 — redact credentials, authorization headers, session tokens,
  and sensitive query parameters from logs and traces.
* Requirement 18.6 — prevent routine diagnostic logs from storing raw secrets.

Design notes:

* Redaction is best-effort and defensive. It errs toward over-redacting a value
  rather than leaking one. It is not a substitute for never logging secrets in
  the first place, but it protects against accidental inclusion.
* The functions never raise on unexpected input shapes; they return a redacted
  copy and leave the original object untouched (no in-place mutation).
* The placeholder is a fixed, non-reversible marker so redacted logs remain
  readable and greppable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: The marker substituted in place of any redacted value.
REDACTED = "***REDACTED***"

#: Header names whose *entire* value must be redacted (case-insensitive).
_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-amz-security-token",
        "x-csrf-token",
        "api-key",
    }
)

#: Dict/mapping keys whose value must be redacted regardless of nesting
#: (case-insensitive, matched as a normalized substring).
_SENSITIVE_KEY_FRAGMENTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "cookie",
    "session",
    "private_key",
    "access_key",
    "client_secret",
)

#: Query-parameter names (case-insensitive) whose value must be redacted.
_SENSITIVE_QUERY_PARAMS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "token",
    "access_token",
    "auth",
    "authorization",
    "key",
    "secret",
    "password",
    "sig",
    "signature",
    "session",
    "sessionid",
)

# --- Precompiled patterns -------------------------------------------------

# ?api_key=VALUE  or  &token=VALUE  (value = everything up to next & or #)
_QUERY_PARAM_RE = re.compile(
    r"(?P<sep>[?&])(?P<name>"
    + "|".join(re.escape(p) for p in _SENSITIVE_QUERY_PARAMS)
    + r")"
    r"(?P<eq>=)(?P<value>[^&#\s]*)",
    re.IGNORECASE,
)

# Authorization: Bearer <token>  /  Authorization: Basic <b64>  in free text.
_AUTH_HEADER_INLINE_RE = re.compile(
    r"(?P<name>authorization|proxy-authorization)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\S.*?)(?=$|[\r\n]|;)",
    re.IGNORECASE,
)

# Cookie / Set-Cookie header rendered inline in a log line.
_COOKIE_HEADER_INLINE_RE = re.compile(
    r"(?P<name>set-cookie|cookie)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\S.*?)(?=$|[\r\n])",
    re.IGNORECASE,
)

# Bearer tokens appearing anywhere in text.
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE)

# Long, high-entropy-looking API-key values (e.g. "sk-...", "tvly-...", AWS AKIA,
# or generic long base62/hex tokens). Kept conservative to avoid mangling
# ordinary identifiers.
_API_KEY_VALUE_RE = re.compile(
    r"\b("
    r"(?:sk|pk|rk|tvly|xoxb|xoxp|ghp|gho|glpat)[-_][A-Za-z0-9\-_]{8,}"  # prefixed keys
    r"|AKIA[0-9A-Z]{16}"  # AWS access key id
    r"|[A-Za-z0-9+/]{40,}={0,2}"  # long base64-ish blobs / AWS secret keys
    r")\b"
)


def _key_is_sensitive(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact_string(text: str) -> str:
    """Redact secrets found in a free-form log string.

    Handles inline Authorization headers, Cookie/Set-Cookie headers, sensitive
    query parameters, Bearer tokens, and API-key-like values. The value portion
    is replaced with :data:`REDACTED` while surrounding structure is preserved
    so the line remains diagnostically useful.
    """
    if not isinstance(text, str) or not text:
        return text

    result = text

    # Order matters: handle structured header/query forms before the broad
    # API-key value sweep so we keep field names intact.
    result = _AUTH_HEADER_INLINE_RE.sub(
        lambda m: f"{m.group('name')}{m.group('sep')}{REDACTED}", result
    )
    result = _COOKIE_HEADER_INLINE_RE.sub(
        lambda m: f"{m.group('name')}{m.group('sep')}{REDACTED}", result
    )
    result = _QUERY_PARAM_RE.sub(
        lambda m: f"{m.group('sep')}{m.group('name')}{m.group('eq')}{REDACTED}", result
    )
    result = _BEARER_RE.sub(REDACTED, result)
    result = _API_KEY_VALUE_RE.sub(REDACTED, result)

    return result


def redact_url(url: str) -> str:
    """Redact sensitive query parameters from a URL.

    A thin wrapper over :func:`redact_string` scoped to the query-parameter and
    API-key rules, useful when logging request URLs.
    """
    if not isinstance(url, str) or not url:
        return url
    result = _QUERY_PARAM_RE.sub(
        lambda m: f"{m.group('sep')}{m.group('name')}{m.group('eq')}{REDACTED}", url
    )
    return _API_KEY_VALUE_RE.sub(REDACTED, result)


def redact_headers(headers: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of an HTTP header mapping with sensitive values redacted.

    Header names are matched case-insensitively against the sensitive-header
    allowlist. Non-sensitive header values pass through unchanged.
    """
    redacted: dict[str, Any] = {}
    for name, value in headers.items():
        if isinstance(name, str) and name.strip().lower() in _SENSITIVE_HEADER_NAMES:
            redacted[name] = REDACTED
        elif isinstance(value, str):
            redacted[name] = redact_string(value)
        else:
            redacted[name] = value
    return redacted


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively redact a structured record (dict of arbitrary nesting).

    Keys whose normalized name matches a sensitive fragment have their value
    fully redacted. String values are scrubbed with :func:`redact_string`.
    Nested mappings and sequences are processed recursively.
    """
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(key, str) and _key_is_sensitive(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = redact_value(value)
    return redacted


def redact_value(value: Any) -> Any:
    """Redact an arbitrary value (string, mapping, sequence, or scalar).

    This is the general dispatch entry point used by :func:`redact_mapping` for
    nested structures. Scalars other than strings are returned unchanged.
    """
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    # Redact items of list/tuple/set-like sequences, but not str/bytes.
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_value(item) for item in value]
    return value
