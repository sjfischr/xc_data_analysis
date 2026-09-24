"""Server-side sanitization of agent-produced Markdown (Requirement 10.8:
"sanitize agent-produced Markdown and SHALL NOT execute agent-produced
HTML, JavaScript, SQL, shell commands, or fetched instructions in the
browser").

The system prompt (``agents/analytics_agent.py``) already instructs the
model to respond in plain Markdown, but a prompt instruction is not a
security control -- a model can be steered (or simply err) into emitting a
literal ``<script>`` tag, an ``onerror=`` attribute, or a ``javascript:``
URI. This module is the actual enforcement, applied server-side before any
agent text reaches a client, so it holds regardless of model behavior.

No markdown-to-HTML renderer exists yet (that is Task 14.4's frontend
concern). This module's job is narrower and prior to that: guarantee that
whatever HTML metacharacters the raw agent text contains can never be
interpreted as live HTML/script by any downstream renderer, by escaping
them to their literal entity form. A future renderer converts *markdown
syntax* (which never uses raw ``<``/``>``/``&``) to safe HTML of its own
generation; it never needs to un-escape model-supplied text to do that.
"""

from __future__ import annotations

import html
import re

# javascript:/data: URI schemes inside a markdown link `[text](javascript:...)`
# survive HTML-escaping (they're not HTML metacharacters) and still need a
# renderer that turns markdown links into anchors to refuse them --
# rewriting the scheme here, defense-in-depth, means even a renderer that
# forgets that check never receives a live-looking `javascript:` URI.
_DANGEROUS_URI_SCHEME_RE = re.compile(r"\b(javascript|data|vbscript):", re.IGNORECASE)
_BLOCKED_SCHEME_PLACEHOLDER = "blocked:"


def sanitize_markdown(text: str) -> str:
    """Return ``text`` safe to store or transmit as agent-produced
    Markdown. Call this exactly once per piece of agent output -- it is
    not idempotent (re-escaping already-escaped text double-escapes
    ``&``), though sanitizing twice by mistake still never reintroduces a
    live tag or dangerous URI scheme."""
    escaped = html.escape(text, quote=True)
    return _DANGEROUS_URI_SCHEME_RE.sub(_BLOCKED_SCHEME_PLACEHOLDER, escaped)
