"""Long-term AgentCore Memory sanitization policy (Task 11.6, design.md
section 12.4, Requirement 11.8).

"Before memory storage, policy removes secrets, credentials, raw source
payloads, hidden reasoning, and unnecessary registration fields" (design.md
12.4). This module is that policy -- applied to opt-in summary/preference
text before it is ever written to AgentCore Memory's long-term store.
Short-term (within-session) context is not filtered through this: it is
never persisted past the session per design.md 12.4's "short-term memory:
enabled for conversational continuity" (ordinary conversational recall),
while only "long-term summary and user-preference strategies" are opt-in
and pass through here.

Semantic fact extraction about athletes is disabled entirely in release 1
(design.md 12.4) -- that is a Memory *strategy* configured off at the
AgentCore resource level (infrastructure, Task 15.3), not a text filter
this module could enforce after the fact, so it is out of this module's
scope by design.
"""

from __future__ import annotations

import re

from xc_platform.security.redaction import redact_string

MAX_LONG_TERM_MEMORY_LENGTH = 2000

# Requirement 13.5's "unnecessary registration fields" (date of birth,
# street address, phone, email) must never reach long-term memory even as
# an incidental mention in a summary -- this is a coarse pattern match, not
# a PII-detection model; it errs toward stripping too much (design.md
# 12.4's redaction posture), consistent with redact_string's own approach.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_DOB_LABEL_RE = re.compile(
    r"\b(date of birth|dob|birthdate)\s*[:\-]?\s*\S.*?(?=[.;\n]|$)", re.IGNORECASE
)
_ADDRESS_LABEL_RE = re.compile(
    r"\b(street address|home address|mailing address)\s*[:\-]?\s*\S.*?(?=[.;\n]|$)",
    re.IGNORECASE,
)

_REDACTED_FIELD = "[REDACTED FIELD]"


def sanitize_for_long_term_memory(text: str) -> str:
    """Return ``text`` safe to persist as opt-in long-term summary/
    preference memory. Truncates to :data:`MAX_LONG_TERM_MEMORY_LENGTH`
    after redaction, never before, so truncation cannot itself cut a
    secret in half and leave a fragment exposed."""
    sanitized = redact_string(text)
    sanitized = _EMAIL_RE.sub(_REDACTED_FIELD, sanitized)
    sanitized = _PHONE_RE.sub(_REDACTED_FIELD, sanitized)
    sanitized = _DOB_LABEL_RE.sub(_REDACTED_FIELD, sanitized)
    sanitized = _ADDRESS_LABEL_RE.sub(_REDACTED_FIELD, sanitized)
    if len(sanitized) > MAX_LONG_TERM_MEMORY_LENGTH:
        sanitized = sanitized[:MAX_LONG_TERM_MEMORY_LENGTH] + "…[truncated]"
    return sanitized
