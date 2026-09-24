"""Ordered, transactional, forward-only SQLite migrations (Task 4.1).

Migration files live in the top-level ``migrations/`` directory (design.md
section 5) as ``NNNN_name.sql``, where ``NNNN`` is a zero-padded, contiguous,
1-based version number. Each file is applied inside a single transaction, and
its version plus a content checksum are recorded in a ``schema_migrations``
tracking table so that:

* a database can never end up with a gap in its applied-migrations history
  (:class:`~xc_platform.db.errors.PartiallyAppliedSchemaError`);
* a database migrated by a *newer* version of this code than the one
  currently running is refused rather than opened and silently corrupted
  (:class:`~xc_platform.db.errors.IncompatibleSchemaError`, Requirement 2.5 --
  migrations are forward-only, so there is no downgrade path);
* a migration file whose content changed after it was already applied to a
  database is detected instead of silently ignored (also
  :class:`~xc_platform.db.errors.IncompatibleSchemaError`).

:func:`bootstrap` is the empty-database entry point: given a filesystem path
that may not exist yet, it opens a writer connection and applies every known
migration in order.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from xc_platform.db.connection import open_writer_connection
from xc_platform.db.errors import IncompatibleSchemaError, PartiallyAppliedSchemaError

_FILENAME_RE = re.compile(r"^(?P<version>\d{4,})_(?P<name>[a-z0-9_]+)\.sql$")

_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    checksum     TEXT NOT NULL,
    applied_at   TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Migration:
    """One loaded migration file."""

    version: int
    name: str
    sql: str
    checksum: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """One row already recorded in ``schema_migrations``."""

    version: int
    name: str
    checksum: str
    applied_at: str


def default_migrations_dir() -> Path:
    """Return the repository's top-level ``migrations/`` directory.

    ``migrator.py`` lives at ``src/xc_platform/db/migrator.py``; the
    migrations directory is three levels up from there, at the repo root.
    """
    return Path(__file__).resolve().parents[3] / "migrations"


def load_migrations(migrations_dir: Path | None = None) -> tuple[Migration, ...]:
    """Load and validate every migration file, sorted by version.

    Raises ``ValueError`` if version numbers are not a contiguous ``1..N``
    sequence with no duplicates -- a code-level authoring error, distinct
    from :class:`PartiallyAppliedSchemaError`, which describes a *database's*
    applied history rather than the migration files on disk.
    """
    directory = migrations_dir or default_migrations_dir()
    found: list[Migration] = []
    for entry in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(entry.name)
        if not match:
            raise ValueError(
                f"Migration file {entry.name!r} does not match the required "
                "NNNN_name.sql naming convention."
            )
        sql = entry.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                sql=sql,
                checksum=checksum,
            )
        )

    found.sort(key=lambda m: m.version)
    expected_versions = list(range(1, len(found) + 1))
    actual_versions = [m.version for m in found]
    if actual_versions != expected_versions:
        raise ValueError(
            "Migration files must be a contiguous 1..N sequence with no "
            f"gaps or duplicates; found versions {actual_versions} in "
            f"{directory}."
        )
    return tuple(found)


def latest_schema_version(migrations_dir: Path | None = None) -> int:
    """Return the highest migration version this code knows about."""
    migrations = load_migrations(migrations_dir)
    return migrations[-1].version if migrations else 0


def _ensure_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_TRACKING_TABLE_SQL)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _applied_migrations(conn: sqlite3.Connection) -> list[AppliedMigration]:
    rows = conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations "
        "ORDER BY version"
    ).fetchall()
    return [
        AppliedMigration(
            version=row["version"],
            name=row["name"],
            checksum=row["checksum"],
            applied_at=row["applied_at"],
        )
        for row in rows
    ]


def _validate_applied(
    conn: sqlite3.Connection, migrations: tuple[Migration, ...]
) -> set[int]:
    applied = _applied_migrations(conn)
    known_by_version = {m.version: m for m in migrations}
    max_known = migrations[-1].version if migrations else 0

    for expected_version, row in enumerate(applied, start=1):
        if row.version != expected_version:
            raise PartiallyAppliedSchemaError(
                "schema_migrations has a gap or out-of-order entry: expected "
                f"version {expected_version} next but found {row.version}. "
                "This database's applied-migrations history is not a clean "
                "1..N sequence and cannot be trusted; it is refused rather "
                "than guessed at."
            )
        if row.version > max_known:
            raise IncompatibleSchemaError(
                f"Database has migration {row.version} ({row.name!r}) "
                f"applied, but this code only knows migrations up to "
                f"{max_known}. Migrations are forward-only; downgrading is "
                "not supported. Deploy a matching or newer code version."
            )
        defined = known_by_version[row.version]
        if defined.checksum != row.checksum:
            raise IncompatibleSchemaError(
                f"Migration {row.version} ({defined.name!r}) content does "
                "not match what was recorded as applied to this database "
                f"(checksum {row.checksum} recorded, {defined.checksum} on "
                "disk). The migration file was edited after being applied; "
                "author a new migration instead of changing history."
            )

    return {row.version for row in applied}


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    from xc_platform.db.identifiers import utc_now_iso

    script = f"BEGIN IMMEDIATE;\n{migration.sql}\nCOMMIT;"
    try:
        conn.executescript(script)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute(
        "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
        "VALUES (?, ?, ?, ?)",
        (migration.version, migration.name, migration.checksum, utc_now_iso()),
    )


def migrate(
    conn: sqlite3.Connection, *, migrations_dir: Path | None = None
) -> list[int]:
    """Apply every pending migration to ``conn`` in order.

    Idempotent: calling this again on an up-to-date database applies
    nothing and returns an empty list. Raises
    :class:`~xc_platform.db.errors.PartiallyAppliedSchemaError` or
    :class:`~xc_platform.db.errors.IncompatibleSchemaError` if the database's
    existing history cannot be trusted (see module docstring).
    """
    migrations = load_migrations(migrations_dir)
    _ensure_tracking_table(conn)
    already_applied = _validate_applied(conn, migrations)

    newly_applied: list[int] = []
    for migration in migrations:
        if migration.version in already_applied:
            continue
        _apply_migration(conn, migration)
        newly_applied.append(migration.version)
    return newly_applied


def bootstrap(
    path: str | Path, *, migrations_dir: Path | None = None
) -> sqlite3.Connection:
    """Open (creating if necessary) ``path`` and apply every migration.

    This is the empty-database bootstrap command required by Task 4.1: a
    fresh, nonexistent SQLite path becomes a fully migrated database in one
    call. The caller owns the returned connection.
    """
    conn = open_writer_connection(path)
    migrate(conn, migrations_dir=migrations_dir)
    return conn
