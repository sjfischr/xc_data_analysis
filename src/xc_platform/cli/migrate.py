"""CLI entry point: bootstrap or migrate a local SQLite database file.

Usage::

    python -m xc_platform.cli.migrate <path-to-db>

Creates ``<path-to-db>`` if it does not exist and applies every pending
migration in order (Task 4.1's "empty-database bootstrap command").
"""

from __future__ import annotations

import sys

from xc_platform.db.migrator import bootstrap


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python -m xc_platform.cli.migrate <path-to-db>", file=sys.stderr)
        return 2

    db_path = args[0]
    conn = bootstrap(db_path)
    try:
        applied = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        conn.close()

    for row in applied:
        print(f"applied: {row['version']:04d}_{row['name']}")
    print(f"{db_path}: {len(applied)} migration(s) applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
