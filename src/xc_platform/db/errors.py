"""Shared exception types for the db package (Requirement 2.5, 2.7)."""

from __future__ import annotations


class SchemaError(RuntimeError):
    """Base class for migration/schema-compatibility failures."""


class PartiallyAppliedSchemaError(SchemaError):
    """The database's applied-migrations history has a gap or duplicate.

    This means ``schema_migrations`` does not record a clean, contiguous
    ``1..N`` sequence -- for example a crash between applying a migration's
    DDL and recording it, or an externally edited tracking table. The
    database is refused rather than guessed at (Requirement 2.5).
    """


class IncompatibleSchemaError(SchemaError):
    """The database's schema cannot be safely opened by this code.

    Raised when the database has a migration applied that is newer than any
    migration this code knows about (a downgrade attempt -- migrations are
    forward-only), or when an applied migration's recorded checksum does not
    match the migration file this code has for that version (schema drift).
    """


class RepositoryError(RuntimeError):
    """Raised when a repository operation cannot be completed as requested."""
