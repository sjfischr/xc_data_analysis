"""S3 key layout (design.md section 7.1).

::

    s3://<data-bucket>/
      database/
        active.json
        locks/writer.json
        snapshots/<publication-id>/xc.db
        reports/<publication-id>/reconciliation.json

Every key derived from a publication ID is a pure function of that ID --
never taken as free-form input -- so a manifest's claimed ``snapshot_key``
can be checked against the key this module would generate for that same ID
(:mod:`xc_platform.db.publication.models`'s path-traversal defense).
"""

from __future__ import annotations

import re

_DATABASE_PREFIX = "database"

ACTIVE_MANIFEST_KEY = f"{_DATABASE_PREFIX}/active.json"
WRITER_LEASE_KEY = f"{_DATABASE_PREFIX}/locks/writer.json"

# UUID4 text as produced by xc_platform.db.identifiers.new_id().
_UUID4_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PUBLICATION_ID_RE = re.compile(f"^{_UUID4_PATTERN}$")
_SNAPSHOT_KEY_RE = re.compile(
    rf"^{_DATABASE_PREFIX}/snapshots/({_UUID4_PATTERN})/xc\.db$"
)


def is_valid_publication_id(publication_id: str) -> bool:
    return bool(_PUBLICATION_ID_RE.match(publication_id))


def snapshot_key(publication_id: str) -> str:
    return f"{_DATABASE_PREFIX}/snapshots/{publication_id}/xc.db"


def snapshot_publication_id_from_key(key: str) -> str | None:
    """Return the publication ID a snapshot key embeds, or ``None`` if the
    key does not exactly match the ``database/snapshots/<uuid>/xc.db``
    layout -- the path-traversal defense: no ``..``, no extra segments, no
    non-UUID text accepted as an S3 key from an untrusted manifest field.

    A manifest's ``snapshot_key`` does not have to reference *that same*
    manifest's own publication ID: restore (design.md section 7.4)
    republishes a new manifest generation pointing at an older, unedited
    immutable snapshot object under a different, historical publication ID.
    What matters is that the key is provably one of *some* real publication's
    canonical snapshot path, never a free-form string.
    """
    match = _SNAPSHOT_KEY_RE.match(key)
    return match.group(1) if match else None


def reconciliation_report_key(publication_id: str) -> str:
    return f"{_DATABASE_PREFIX}/reports/{publication_id}/reconciliation.json"
