"""Publish the writer's current state as a new generation (Task 19.3).

Ingest commits publish through :func:`xc_platform.ingest.workflow.commit_scope`;
an owner correction (e.g. renaming a school) changes canonical data outside
any ingest run and needs the same publish-then-record sequence so readers
see it and the next commit's parent is right.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from xc_platform.db.publication.models import Manifest
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.publications import PublicationRepository


def publish_writer_state(
    conn: sqlite3.Connection,
    db_path: Path,
    publisher: SnapshotPublisher,
    *,
    created_by: str,
    label: str,
) -> Manifest:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    publications = PublicationRepository(conn)
    parent = publications.get_active()
    summary = {"corrections": 1}
    manifest = publisher.publish(
        db_path,
        created_by=created_by,
        ingest_run_id=label,
        summary=summary,
        parent_publication_id=parent.publication_id if parent else None,
    )
    publications.record_publication(
        publication_id=manifest.publication_id,
        snapshot_key=manifest.snapshot_key,
        snapshot_version_id=manifest.snapshot_version_id,
        content_sha256=manifest.sha256,
        byte_size=manifest.byte_size,
        schema_version=manifest.schema_version,
        summary_json=json.dumps(summary),
        parent_publication_id=manifest.parent_publication_id,
    )
    return manifest
