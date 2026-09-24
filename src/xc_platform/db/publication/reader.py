"""Verified read-only snapshot manager (Task 6.2, design.md section 7.2).

Requirements: R3.1-R3.3, R3.7, R20.6-R20.7.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from xc_platform.db.connection import open_reader_connection
from xc_platform.db.publication import layout
from xc_platform.db.publication.errors import (
    ActiveManifestMissingError,
    ObjectNotFoundError,
    SnapshotVerificationError,
)
from xc_platform.db.publication.models import Manifest
from xc_platform.db.publication.s3_client import S3Client
from xc_platform.db.publication.verification import verify_for_read


@dataclass(frozen=True, slots=True)
class PinnedSnapshot:
    """One verified, immutable local snapshot, pinned for a request or session.

    Requirement 3.6: callers include ``manifest.publication_id`` in
    analytical provenance for the duration of one request or agent
    conversation, rather than re-resolving "current" mid-conversation.
    """

    manifest: Manifest
    local_path: Path

    def open_connection(self) -> sqlite3.Connection:
        return open_reader_connection(self.local_path)


class SnapshotReader:
    """Fetches, verifies, and caches the active published snapshot.

    Design.md section 7.2:

    1. Fetch ``active.json`` (a short cache TTL avoids hitting S3 on every
       call -- :meth:`current` only re-checks after ``cache_ttl_seconds``).
    2. If the referenced publication is already the locally pinned one,
       reuse it without a snapshot download.
    3. Otherwise download the immutable snapshot to a temporary path.
    4. Verify size, SHA-256, and ``PRAGMA quick_check`` before trusting it.
    5. Rename atomically into place and hand back a read-only, immutable
       connection factory.
    6. Retain the prior verified local file for one generation: a failed
       refresh never evicts the previous valid snapshot (Requirement 3.7).
    """

    def __init__(
        self,
        s3: S3Client,
        *,
        cache_dir: Path,
        cache_ttl_seconds: float = 30.0,
    ) -> None:
        self._s3 = s3
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._current: PinnedSnapshot | None = None
        self._active_etag: str | None = None
        self._last_checked_monotonic: float | None = None
        self.last_refresh_error: Exception | None = None

    def current(self) -> PinnedSnapshot:
        """Return the pinned current snapshot, refreshing if the TTL elapsed.

        A refresh failure is recorded on :attr:`last_refresh_error` and
        does not raise here as long as a previously verified snapshot is
        available -- read-only dashboard functions keep working from the
        last valid snapshot (Requirement 20.6).
        """
        if self._should_check():
            try:
                self._refresh()
                self.last_refresh_error = None
            except Exception as exc:
                self.last_refresh_error = exc
                if self._current is None:
                    raise
            finally:
                self._last_checked_monotonic = time.monotonic()

        if self._current is None:
            raise RuntimeError(
                "no verified snapshot is available yet"
                + (
                    f" (last error: {self.last_refresh_error})"
                    if self.last_refresh_error
                    else ""
                )
            )
        return self._current

    def check_now(self) -> None:
        """Make the next :meth:`current` re-check ``active.json`` instead of
        waiting out the TTL -- called right after this process publishes,
        so the publishing administrator sees their own change at once."""
        self._last_checked_monotonic = None

    def _should_check(self) -> bool:
        return (
            self._current is None
            or self._last_checked_monotonic is None
            or (time.monotonic() - self._last_checked_monotonic)
            >= self._cache_ttl_seconds
        )

    def _refresh(self) -> None:
        head = self._s3.head_object(layout.ACTIVE_MANIFEST_KEY)
        if head is None:
            raise ActiveManifestMissingError(
                f"no object at {layout.ACTIVE_MANIFEST_KEY!r}; nothing has been "
                "published yet"
            )
        if self._current is not None and head.etag == self._active_etag:
            return  # unchanged since the last check

        manifest_tmp = self._cache_dir / f".active-{uuid.uuid4().hex}.json.tmp"
        try:
            self._s3.get_object(layout.ACTIVE_MANIFEST_KEY, manifest_tmp)
            manifest = Manifest.from_json(manifest_tmp.read_text(encoding="utf-8"))
        finally:
            manifest_tmp.unlink(missing_ok=True)

        if (
            self._current is not None
            and manifest.publication_id == self._current.manifest.publication_id
        ):
            self._active_etag = head.etag
            return

        snapshot_tmp = (
            self._cache_dir
            / f".snapshot-{manifest.publication_id}-{uuid.uuid4().hex}.tmp"
        )
        try:
            self._s3.get_object(manifest.snapshot_key, snapshot_tmp)
        except ObjectNotFoundError as exc:
            snapshot_tmp.unlink(missing_ok=True)
            raise SnapshotVerificationError(
                f"active manifest references {manifest.snapshot_key!r}, which "
                "does not exist in S3"
            ) from exc

        result = verify_for_read(
            snapshot_tmp,
            expected_sha256=manifest.sha256,
            expected_byte_size=manifest.byte_size,
        )
        if not result.verified:
            snapshot_tmp.unlink(missing_ok=True)
            raise SnapshotVerificationError(
                f"downloaded snapshot for publication {manifest.publication_id} "
                f"failed verification: {result.reason}"
            )

        final_path = self._cache_dir / f"xc-{manifest.publication_id}.db"
        snapshot_tmp.replace(final_path)  # atomic rename within the cache dir

        previous = self._current
        self._current = PinnedSnapshot(manifest=manifest, local_path=final_path)
        self._active_etag = head.etag
        if previous is not None and previous.local_path != final_path:
            previous.local_path.unlink(missing_ok=True)
