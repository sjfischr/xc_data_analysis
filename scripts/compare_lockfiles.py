#!/usr/bin/env python3
"""Compare a committed pip-compile lockfile with a fresh re-lock (CI).

The lockfiles are compiled on Windows, where pip-compile adds two packages
that a Linux re-lock leaves out:

- ``colorama`` -- pytest needs it on Windows. pip-compile drops the
  ``sys_platform == "win32"`` marker when it compiles there, so the entry
  carries no marker.
- ``pywin32`` -- needed by ``mcp`` on Windows, and kept with its marker.

A Linux-compiled lock would leave these out and break ``--require-hashes``
installs on Windows, so the Windows lock is the one to keep. This check
(2026-10-17) therefore allows exactly those Windows-only entries and fails
on any other difference: a package added or removed, a version, or a hash
set. The header comment is ignored; it records the compiling Python only.

Usage::

    python scripts/compare_lockfiles.py COMMITTED FRESH [--github]

Exit status 0 when the files agree, 1 when they don't.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Windows-only packages pip-compile emits without a marker when it runs on
# Windows. An entry that does carry a win32 marker is allowed automatically.
WINDOWS_ONLY_UNMARKED = frozenset({"colorama"})

_ENTRY = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==(\S+?)(\s*;\s*(.+?))?\s*\\?$"
)
_HASH = re.compile(r"--hash=(sha256:[0-9a-f]{64})")


@dataclass
class Entry:
    version: str
    marker: str | None
    hashes: set[str] = field(default_factory=set)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lockfile(text: str) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    current: Entry | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENTRY.match(line)
        if match:
            name, _extras, version, _, marker = match.groups()
            current = Entry(version=version, marker=marker)
            entries[_normalize(name)] = current
        elif current is not None:
            current.hashes.update(_HASH.findall(line))
    return entries


def _windows_only(name: str, entry: Entry) -> bool:
    return name in WINDOWS_ONLY_UNMARKED or (
        entry.marker is not None and 'sys_platform == "win32"' in entry.marker
    )


def compare(committed: str, fresh: str) -> list[str]:
    """Differences that mean the committed lock is out of date."""
    old, new = parse_lockfile(committed), parse_lockfile(fresh)
    problems: list[str] = []
    for name in sorted(old.keys() - new.keys()):
        if not _windows_only(name, old[name]):
            problems.append(f"{name}=={old[name].version} is no longer resolved")
    for name in sorted(new.keys() - old.keys()):
        problems.append(f"{name}=={new[name].version} is missing from the lock")
    for name in sorted(old.keys() & new.keys()):
        before, after = old[name], new[name]
        if before.version != after.version:
            problems.append(
                f"{name}: locked {before.version}, resolves {after.version}"
            )
        elif before.hashes != after.hashes:
            problems.append(f"{name}=={before.version}: hash set differs")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("committed", type=Path)
    parser.add_argument("fresh", type=Path)
    parser.add_argument("--github", action="store_true", help="emit ::error:: lines")
    args = parser.parse_args(argv)
    problems = compare(
        args.committed.read_text(encoding="utf-8"),
        args.fresh.read_text(encoding="utf-8"),
    )
    for problem in problems:
        print(f"::error::{problem}" if args.github else problem)
    if problems:
        print(
            f"{args.committed} is out of date with its .in file; re-lock it "
            "(see docs/dependency-review.md)."
        )
        return 1
    print(f"{args.committed} is in sync (Windows-only entries allowed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
