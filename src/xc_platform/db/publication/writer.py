"""Single-writer publisher, and restore (Task 6.3-6.4, design.md section 7.3-7.4).

Requirements: R3.4-R3.6, R3.8-R3.10, R7.8, R18.2, R19.7.

FIFO serialization and reserved concurrency 1 (design.md section 7.3 prose)
are the primary concurrency boundary in production, enforced by the SQS
import queue (Task 9's infrastructure, not this module). The writer lease
and manifest compare-and-swap implemented here are the correctness
boundary that holds even if that primary boundary fails -- "manifest
compare-and-swap prevents lost updates even if a lease mechanism fails."

An orphaned candidate snapshot (uploaded, but never activated because the
active-manifest compare-and-swap lost a race) is identified here via
:class:`~xc_platform.db.publication.errors.PublicationConflictError`'s
``orphaned_publication_id``/``orphaned_snapshot_key`` attributes so a caller
can log or alert on it. Actually deleting an orphaned object is an S3
bucket lifecycle policy concern (infrastructure-as-code, Task 13), not
something this module does synchronously -- the immutable snapshot object
is harmless to leave in place (it is never referenced by any active or
historical manifest) until that policy expires it.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.publication import layout
from xc_platform.db.publication.errors import (
    PreconditionFailedError,
    PublicationConflictError,
    SnapshotVerificationError,
    WriterBusyError,
)
from xc_platform.db.publication.models import Manifest, WriterLease
from xc_platform.db.publication.s3_client import S3Client
from xc_platform.db.publication.verification import verify_for_read, verify_for_write

DEFAULT_LEASE_TTL_SECONDS = 300.0


def _schema_version_of(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


class SnapshotPublisher:
    def __init__(
        self,
        s3: S3Client,
        *,
        work_dir: Path,
        owner_id: str,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        self._s3 = s3
        self._work_dir = work_dir
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._owner_id = owner_id
        self._lease_ttl_seconds = lease_ttl_seconds

    # --- lease -------------------------------------------------------

    def _acquire_lease(self, *, ingest_run_id: str) -> str:
        """Acquire the writer lease, taking over an expired one if needed.

        Returns the ETag of the lease object this call wrote, so
        :meth:`_release_lease` can release conditionally -- it must never
        delete a lease some other writer has since taken over.
        """
        lease = WriterLease.new(
            owner_id=self._owner_id,
            ingest_run_id=ingest_run_id,
            ttl_seconds=self._lease_ttl_seconds,
        )
        lease_path = self._work_dir / f".lease-{uuid.uuid4().hex}.json"
        lease_path.write_text(lease.to_json(), encoding="utf-8")
        try:
            try:
                meta = self._s3.put_object(
                    layout.WRITER_LEASE_KEY, lease_path, if_none_match=True
                )
                return meta.etag
            except PreconditionFailedError:
                pass  # a lease is already held; see if it has expired

            existing_meta = self._s3.head_object(layout.WRITER_LEASE_KEY)
            if existing_meta is None:
                # Raced with a release between the two calls above; retry once.
                meta = self._s3.put_object(
                    layout.WRITER_LEASE_KEY, lease_path, if_none_match=True
                )
                return meta.etag

            existing_tmp = self._work_dir / f".existing-lease-{uuid.uuid4().hex}.json"
            try:
                self._s3.get_object(layout.WRITER_LEASE_KEY, existing_tmp)
                existing = WriterLease.from_json(
                    existing_tmp.read_text(encoding="utf-8")
                )
            finally:
                existing_tmp.unlink(missing_ok=True)

            if not existing.is_expired():
                raise WriterBusyError(
                    f"writer lease held by {existing.owner_id!r} (ingest_run "
                    f"{existing.ingest_run_id!r}), expires {existing.expires_at}"
                )

            try:
                meta = self._s3.put_object(
                    layout.WRITER_LEASE_KEY, lease_path, if_match=existing_meta.etag
                )
                return meta.etag
            except PreconditionFailedError as exc:
                raise WriterBusyError(
                    "writer lease was replaced by another writer while taking "
                    "over its expiry"
                ) from exc
        finally:
            lease_path.unlink(missing_ok=True)

    def _release_lease(self, lease_etag: str) -> None:
        try:
            self._s3.delete_object(layout.WRITER_LEASE_KEY, if_match=lease_etag)
        except PreconditionFailedError:
            # Another writer already took over this (expired) lease; it is
            # no longer ours to delete.
            pass

    # --- activation (shared by publish and restore) -------------------

    def _activate(self, manifest: Manifest, *, expect_bootstrap: bool) -> None:
        """Compare-and-swap ``active.json`` to ``manifest``.

        ``expect_bootstrap=True`` means the caller believes no active
        manifest exists yet and uses create-only semantics; otherwise the
        write is conditional on the currently observed ETag. Either way, a
        losing race leaves the previously active generation completely
        unchanged (Requirement 3.5) and raises
        :class:`PublicationConflictError` naming the orphaned candidate.
        """
        manifest_path = self._work_dir / f".manifest-{manifest.publication_id}.json"
        manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        try:
            active_meta = self._s3.head_object(layout.ACTIVE_MANIFEST_KEY)
            if active_meta is None:
                if not expect_bootstrap:
                    raise PublicationConflictError(
                        "no active.json exists, but this publish was not a "
                        "bootstrap publish (a parent_publication_id was given); "
                        f"publication {manifest.publication_id} was uploaded to "
                        f"{manifest.snapshot_key} but never activated",
                        orphaned_publication_id=manifest.publication_id,
                        orphaned_snapshot_key=manifest.snapshot_key,
                    )
                self._s3.put_object(
                    layout.ACTIVE_MANIFEST_KEY, manifest_path, if_none_match=True
                )
                return
            if expect_bootstrap:
                raise PublicationConflictError(
                    "active.json already exists, but this publish had no "
                    "parent_publication_id (a bootstrap publish); publication "
                    f"{manifest.publication_id} was uploaded to "
                    f"{manifest.snapshot_key} but never activated -- pass the "
                    "current active publication as parent_publication_id "
                    "instead of treating this as the first-ever publish",
                    orphaned_publication_id=manifest.publication_id,
                    orphaned_snapshot_key=manifest.snapshot_key,
                )
            self._s3.put_object(
                layout.ACTIVE_MANIFEST_KEY, manifest_path, if_match=active_meta.etag
            )
        except PreconditionFailedError as exc:
            raise PublicationConflictError(
                "active.json changed since it was read; publication "
                f"{manifest.publication_id} was uploaded to "
                f"{manifest.snapshot_key} but never activated (orphaned -- "
                "harmless to leave; an S3 lifecycle policy reclaims it)",
                orphaned_publication_id=manifest.publication_id,
                orphaned_snapshot_key=manifest.snapshot_key,
            ) from exc
        finally:
            manifest_path.unlink(missing_ok=True)

    # --- publish -------------------------------------------------------

    def publish(
        self,
        local_db_path: Path,
        *,
        created_by: str,
        ingest_run_id: str,
        summary: dict[str, int],
        parent_publication_id: str | None = None,
        reconciliation_report_path: Path | None = None,
    ) -> Manifest:
        """Publish ``local_db_path`` as a new generation.

        The caller has already fully prepared ``local_db_path`` (every
        change for this generation applied and locally committed); this
        method never mutates it. It verifies, uploads immutably, and
        activates via compare-and-swap.

        ``reconciliation_report_path``, if given, is uploaded alongside the
        snapshot at ``database/reports/<publication-id>/reconciliation.json``
        (design.md section 7.1) -- listable via this generation's manifest,
        the same way :meth:`~xc_platform.db.repositories.publications.
        PublicationRepository.list_lineage` lists publications from the
        database's own ``db_publications`` table (Task 6.4).
        """
        lease_etag = self._acquire_lease(ingest_run_id=ingest_run_id)
        try:
            check = verify_for_write(local_db_path)
            if not check.verified:
                raise SnapshotVerificationError(
                    f"local database failed pre-publish verification: {check.reason}"
                )

            publication_id = new_id()
            snapshot_key = layout.snapshot_key(publication_id)
            self._s3.put_object(snapshot_key, local_db_path, if_none_match=True)
            snapshot_meta = self._s3.head_object(snapshot_key)
            if snapshot_meta is None:
                raise SnapshotVerificationError(
                    f"uploaded snapshot {snapshot_key!r} is not visible "
                    "immediately after upload"
                )

            if reconciliation_report_path is not None:
                self._s3.put_object(
                    layout.reconciliation_report_key(publication_id),
                    reconciliation_report_path,
                    if_none_match=True,
                )

            manifest = Manifest(
                publication_id=publication_id,
                parent_publication_id=parent_publication_id,
                snapshot_key=snapshot_key,
                snapshot_version_id=snapshot_meta.version_id or "",
                sha256=check.sha256,
                byte_size=check.byte_size,
                schema_version=_schema_version_of(local_db_path),
                created_at=utc_now_iso(),
                created_by=created_by,
                summary=summary,
            )
            self._activate(manifest, expect_bootstrap=parent_publication_id is None)
            return manifest
        finally:
            self._release_lease(lease_etag)

    # --- restore (Task 6.4) --------------------------------------------

    def restore(
        self,
        *,
        target_snapshot_key: str,
        target_sha256: str,
        target_byte_size: int,
        target_schema_version: int,
        current_active_publication_id: str | None,
        created_by: str,
        ingest_run_id: str,
        summary: dict[str, int],
    ) -> Manifest:
        """Publish a new generation pointing at an unedited historical snapshot.

        Restore never edits or re-uploads the old snapshot object
        (design.md section 7.4) -- only a new manifest is written, with the
        currently active publication as its parent, preserving an
        append-only lineage. Re-verifies the historical object against its
        recorded checksum before activating it: a corrupted or since-deleted
        historical object is refused rather than restored onto.
        """
        if layout.snapshot_publication_id_from_key(target_snapshot_key) is None:
            raise SnapshotVerificationError(
                f"{target_snapshot_key!r} is not a valid snapshot key"
            )

        lease_etag = self._acquire_lease(ingest_run_id=ingest_run_id)
        try:
            verify_tmp = self._work_dir / f".restore-verify-{uuid.uuid4().hex}.tmp"
            try:
                snapshot_meta = self._s3.get_object(target_snapshot_key, verify_tmp)
                result = verify_for_read(
                    verify_tmp,
                    expected_sha256=target_sha256,
                    expected_byte_size=target_byte_size,
                )
            finally:
                verify_tmp.unlink(missing_ok=True)
            if not result.verified:
                raise SnapshotVerificationError(
                    f"restore target {target_snapshot_key!r} failed "
                    f"verification: {result.reason}"
                )

            manifest = Manifest(
                publication_id=new_id(),
                parent_publication_id=current_active_publication_id,
                snapshot_key=target_snapshot_key,
                snapshot_version_id=snapshot_meta.version_id or "",
                sha256=target_sha256,
                byte_size=target_byte_size,
                schema_version=target_schema_version,
                created_at=utc_now_iso(),
                created_by=created_by,
                summary=summary,
            )
            self._activate(
                manifest, expect_bootstrap=current_active_publication_id is None
            )
            return manifest
        finally:
            self._release_lease(lease_etag)
