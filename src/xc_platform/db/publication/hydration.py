"""Hydrate the single writer's local database from the active snapshot
(Task 19.0).

Found during the 2026-09-23 workplan review: the production API created an
empty, freshly migrated writer database at startup instead of starting from
the published data. A commit from that empty base would have tried to
publish an empty-plus-new-rows snapshot as a bootstrap generation; the
publisher's compare-and-swap refused it (``active.json`` already exists),
so nothing was lost -- but nothing could ever be ingested either.

This copies the verified, pinned active snapshot into place as the writer's
file, migrates it forward if the running code is newer, and records the
active manifest in ``db_publications`` so the next commit's
``parent_publication_id`` is the generation actually live in S3.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.publication.errors import ActiveManifestMissingError
from xc_platform.db.publication.models import Manifest
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.repositories.publications import PublicationRepository


def hydrate_writer_database(
    reader: SnapshotReader, db_path: Path
) -> tuple[sqlite3.Connection, Manifest | None]:
    """Open the writer connection at ``db_path``, seeded from S3.

    Returns the open, migrated writer connection and the manifest it was
    seeded from -- ``None`` only when nothing has ever been published, the
    one case where starting from an empty migrated database is correct (the
    next commit is then a legitimate bootstrap publish). Any other failure
    to fetch or verify the snapshot propagates: starting the writer from an
    empty base while live data exists is exactly the bug this fixes.
    """
    try:
        pinned = reader.current()
    except ActiveManifestMissingError:
        conn = open_writer_connection(db_path)
        migrate(conn)
        return conn, None

    tmp_path = db_path.with_name(db_path.name + ".hydrate.tmp")
    shutil.copyfile(pinned.local_path, tmp_path)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.replace(tmp_path, db_path)

    conn = open_writer_connection(db_path)
    migrate(conn)
    _record_active_manifest(PublicationRepository(conn), pinned.manifest)
    return conn, pinned.manifest


def _record_active_manifest(
    publications: PublicationRepository, manifest: Manifest
) -> None:
    # A published snapshot never contains its own db_publications row: the
    # writer records a publication only after it is activated, by which
    # point the snapshot file is already uploaded and immutable.
    if publications.get(manifest.publication_id) is not None:
        return
    with publications.transaction() as conn:
        conn.execute(
            "UPDATE db_publications SET status = 'superseded' WHERE status = 'active'"
        )
    publications.record_publication(
        publication_id=manifest.publication_id,
        snapshot_key=manifest.snapshot_key,
        snapshot_version_id=manifest.snapshot_version_id,
        content_sha256=manifest.sha256,
        byte_size=manifest.byte_size,
        schema_version=manifest.schema_version,
        summary_json=json.dumps(manifest.summary, sort_keys=True),
        parent_publication_id=None,
    )
