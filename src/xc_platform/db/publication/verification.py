"""Snapshot verification shared by the reader and writer (design.md 7.2-7.3).

The reader path uses ``PRAGMA quick_check`` (fast, runs on every refresh);
the writer path additionally runs ``PRAGMA foreign_key_check`` (thorough,
runs once per publish). The SHA-256 check is never skipped or treated as
redundant with either: a snapshot can pass ``integrity_check`` while still
not being the exact byte sequence that was published (design.md section
7.2 -- confirmed live in Task 3.4 with a snapshot that had 64 bytes zeroed
in its middle and still passed ``PRAGMA integrity_check``).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified: bool
    size_matches: bool
    sha256_matches: bool
    quick_check: str
    reason: str | None = None


def verify_for_read(
    path: Path, *, expected_sha256: str, expected_byte_size: int
) -> VerificationResult:
    """Reader-path verification (design.md section 7.2, step 4)."""
    actual_size = path.stat().st_size
    size_matches = actual_size == expected_byte_size
    actual_sha256 = sha256_of_file(path)
    sha256_matches = actual_sha256 == expected_sha256

    # The SHA-256 check fails closed on its own, independent of what SQLite
    # reports -- run quick_check regardless so a caller always has both
    # signals, but the digest mismatch alone is disqualifying.
    quick_check = "skipped (size mismatch)"
    if size_matches:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
        try:
            quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            quick_check = f"not a valid SQLite database: {exc}"
        finally:
            conn.close()

    verified = size_matches and sha256_matches and quick_check == "ok"
    reason = None
    if not verified:
        problems = []
        if not size_matches:
            problems.append(f"size {actual_size} != expected {expected_byte_size}")
        if not sha256_matches:
            problems.append(f"sha256 {actual_sha256} != expected {expected_sha256}")
        if size_matches and quick_check != "ok":
            problems.append(f"quick_check={quick_check!r}")
        reason = "; ".join(problems)

    return VerificationResult(
        verified=verified,
        size_matches=size_matches,
        sha256_matches=sha256_matches,
        quick_check=quick_check,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class WriteVerificationResult:
    verified: bool
    integrity_check: str
    foreign_key_violations: int
    sha256: str
    byte_size: int
    reason: str | None = None


def verify_for_write(path: Path) -> WriteVerificationResult:
    """Writer-path pre-publish verification (design.md section 7.3).

    Runs the full ``integrity_check`` and ``foreign_key_check`` (not the
    lighter ``quick_check`` the reader uses) since this runs once per
    publish, not on every refresh, and computes the SHA-256/size that will
    go into the new manifest.
    """
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        integrity_check = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = len(
            conn.execute("PRAGMA foreign_key_check").fetchall()
        )
    except sqlite3.DatabaseError as exc:
        integrity_check = f"not a valid SQLite database: {exc}"
        foreign_key_violations = 0
    finally:
        conn.close()

    verified = integrity_check == "ok" and foreign_key_violations == 0
    reason = None
    if not verified:
        problems = []
        if integrity_check != "ok":
            problems.append(f"integrity_check={integrity_check!r}")
        if foreign_key_violations:
            problems.append(f"{foreign_key_violations} foreign_key_check violation(s)")
        reason = "; ".join(problems)

    return WriteVerificationResult(
        verified=verified,
        integrity_check=integrity_check,
        foreign_key_violations=foreign_key_violations,
        sha256=sha256_of_file(path),
        byte_size=path.stat().st_size,
        reason=reason,
    )
