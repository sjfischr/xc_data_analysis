"""UUID and UTC-timestamp helpers shared by migrations and repositories.

Design invariant (design.md section 8): every identifier is UUID text, and
every timestamp is UTC ISO-8601 text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    """Return a new random UUID as text, suitable for any *_id column."""
    return str(uuid.uuid4())


def utc_now_iso() -> str:
    """Return the current time as a UTC ISO-8601 string with a 'Z' suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
