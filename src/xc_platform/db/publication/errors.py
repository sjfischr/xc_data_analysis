"""Shared exception types for the publication package (Requirement 3)."""

from __future__ import annotations


class InvalidManifestError(ValueError):
    """A manifest document failed validation (Task 6.1).

    Covers a malformed document, a snapshot key that doesn't match its
    publication ID (path traversal defense), an unsupported schema version,
    or any other structural problem -- the manifest is refused before any
    S3 object it references is trusted.
    """


class SnapshotVerificationError(RuntimeError):
    """A downloaded snapshot failed size, checksum, or integrity verification.

    Raised only after size/SHA-256/``PRAGMA quick_check`` have all been
    attempted so the message can say exactly which check failed
    (Requirement 3.3, 3.9: never activate an unverified snapshot).
    """


class WriterBusyError(RuntimeError):
    """Another writer currently holds an unexpired publication lease."""


class PublicationConflictError(RuntimeError):
    """The active manifest changed between read and compare-and-swap.

    The previously active generation is left untouched; the caller should
    refresh and, if still appropriate, retry (Requirement 3.5).

    ``orphaned_publication_id``/``orphaned_snapshot_key`` identify the
    candidate snapshot that was uploaded but never activated, so a caller
    can log or alert on it (Task 6.3's "lifecycle handling for orphan
    candidate snapshots" -- actual deletion is an S3 bucket lifecycle
    policy concern, not something raising this exception does).
    """

    def __init__(
        self,
        message: str,
        *,
        orphaned_publication_id: str | None = None,
        orphaned_snapshot_key: str | None = None,
    ) -> None:
        super().__init__(message)
        self.orphaned_publication_id = orphaned_publication_id
        self.orphaned_snapshot_key = orphaned_snapshot_key


class ActiveManifestMissingError(RuntimeError):
    """No ``active.json`` exists yet and the caller did not request bootstrap."""


class PreconditionFailedError(RuntimeError):
    """An S3 conditional write's precondition (If-None-Match/If-Match) failed.

    Raised by :mod:`xc_platform.db.publication.s3_client` implementations;
    callers interpret it as "the key already exists" (create-only) or "the
    key changed since I last read it" (compare-and-swap), depending on
    which precondition was used.
    """


class ObjectNotFoundError(RuntimeError):
    """The requested S3 object does not exist."""
