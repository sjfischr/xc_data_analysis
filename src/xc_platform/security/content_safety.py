"""Untrusted-content safety for fetched web text (Requirement 6.6).

Web pages returned by discovery are data, never instructions. This module
only *detects and reports* instruction-like text; it never acts on it, and
callers must not either -- flag it in the run record for administrator
review, and never let it change what an agent or adapter does.
"""

from __future__ import annotations

import re

# Patterns that, if present in fetched content, indicate an attempt to steer
# an agent or a downstream process. Recorded as a signal; never obeyed.
_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore (?:all |any )?(?:previous|prior|above) instructions",
    r"disregard (?:the )?(?:previous|prior|above)",
    r"you are now",
    r"system prompt",
    r"</?(?:system|assistant|instructions)>",
    r"run this (?:script|command)",
    r"curl\s+https?://",
)


def scan_for_injection(text: str) -> list[str]:
    """Return every instruction-like pattern found in ``text``.

    An empty list means nothing suspicious was found -- it does not mean
    the content is safe to execute or treat as instructions; fetched
    content is never executed regardless.
    """
    found: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(pattern)
    return found
