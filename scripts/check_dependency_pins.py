#!/usr/bin/env python3
"""Verify Python dependency pinning and lockfile integrity (Requirement 16.7).

Checks performed:

1. ``requirements.in`` contains only exact ``==`` pins (no open ranges).
2. ``requirements.txt`` contains only exact ``==`` pins.
3. ``requirements.txt`` (the legacy Heroku install path) does not disagree with
   ``requirements.in``.
4. Every pinned entry in ``requirements.lock`` carries at least one ``--hash``,
   so ``pip install --require-hashes`` cannot silently skip verification.

This runs in CI and is safe to run locally. It reads dependency metadata only:
it never reads credentials, never touches the network, and never installs
anything.

Exit code 0 means every check passed. Exit code 1 lists the problems.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A requirement line that is an exact pin, e.g. "pandas==2.2.3".
EXACT_PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s;]+)$")

# Anything that expresses a range rather than a single version.
RANGE_OPERATORS = (">=", "<=", "~=", ">", "<", "!=", "^", "*")


def normalize(name: str) -> str:
    """Normalize a distribution name for comparison (PEP 503-ish)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def strip_comment(raw: str) -> str:
    return raw.split("#", 1)[0].strip()


def read_direct_pins(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return (pins, problems) for a human-edited requirements file."""
    pins: dict[str, str] = {}
    problems: list[str] = []

    # utf-8-sig so a leading byte-order mark (which pip tolerates) does not
    # corrupt the first requirement name and produce a bogus failure.
    for lineno, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = strip_comment(raw)
        if not line:
            continue
        # Directives like "-r other.txt" or "--index-url ..." are not pins.
        if line.startswith("-"):
            problems.append(f"{path.name}:{lineno}: unsupported directive: {line}")
            continue

        match = EXACT_PIN.match(line)
        if not match:
            reason = "not an exact '==' pin"
            for op in RANGE_OPERATORS:
                if op in line:
                    reason = f"uses open range operator '{op}'"
                    break
            problems.append(f"{path.name}:{lineno}: {reason}: {line}")
            continue

        name = normalize(match.group("name"))
        version = match.group("version")
        if name in pins and pins[name] != version:
            problems.append(
                f"{path.name}:{lineno}: {name} pinned twice with different "
                f"versions ({pins[name]} and {version})"
            )
        pins[name] = version

    return pins, problems


def read_lock_entries(path: Path) -> list[tuple[str, str, bool]]:
    """Return (name, version, has_hash) for each pinned entry in a lockfile.

    pip-compile writes one requirement across several backslash-continued
    lines, so continuations are joined before parsing.
    """
    text = path.read_text(encoding="utf-8-sig")
    # Normalize CRLF first so the continuation join works on either platform.
    logical = text.replace("\r\n", "\n").replace("\\\n", " ")

    entries: list[tuple[str, str, bool]] = []
    for line in logical.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = stripped.split()[0]
        match = EXACT_PIN.match(head)
        if not match:
            continue
        entries.append(
            (
                normalize(match.group("name")),
                match.group("version"),
                "--hash=" in stripped,
            )
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root (defaults to the parent of scripts/).",
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="Emit GitHub Actions ::error:: annotations.",
    )
    args = parser.parse_args()

    root: Path = args.root
    req_in = root / "requirements.in"
    req_txt = root / "requirements.txt"
    req_lock = root / "requirements.lock"

    problems: list[str] = []

    for path in (req_in, req_txt, req_lock):
        if not path.exists():
            problems.append(f"missing required file: {path.name}")
    if problems:
        emit(problems, args.github)
        return 1

    source_pins, source_problems = read_direct_pins(req_in)
    legacy_pins, legacy_problems = read_direct_pins(req_txt)
    problems += source_problems + legacy_problems

    # requirements.txt must not contradict requirements.in.
    for name, version in sorted(legacy_pins.items()):
        if name not in source_pins:
            problems.append(
                f"{name} is pinned in requirements.txt but absent from "
                f"requirements.in (requirements.in is the source of truth)"
            )
        elif source_pins[name] != version:
            problems.append(
                f"{name} pinned {version} in requirements.txt but "
                f"{source_pins[name]} in requirements.in"
            )

    lock_entries = read_lock_entries(req_lock)
    if not lock_entries:
        problems.append(
            "requirements.lock contains no pinned requirements; regenerate it "
            "with: pip-compile --generate-hashes --output-file requirements.lock "
            "requirements.in"
        )

    unhashed = sorted({name for name, _, has_hash in lock_entries if not has_hash})
    for name in unhashed:
        problems.append(f"requirements.lock entry has no --hash: {name}")

    # Every direct dependency must actually appear in the lock.
    locked_names = {name for name, _, _ in lock_entries}
    for name, version in sorted(source_pins.items()):
        if name not in locked_names:
            problems.append(
                f"{name} is in requirements.in but missing from "
                f"requirements.lock; re-run pip-compile"
            )
        else:
            locked_versions = {v for n, v, _ in lock_entries if n == name}
            if version not in locked_versions:
                problems.append(
                    f"{name} pinned {version} in requirements.in but "
                    f"{sorted(locked_versions)} in requirements.lock"
                )

    if problems:
        emit(problems, args.github)
        return 1

    print(
        f"OK: {len(source_pins)} direct pins, {len(legacy_pins)} legacy pins, "
        f"{len(lock_entries)} locked requirements, all hashed."
    )
    return 0


def emit(problems: list[str], github: bool) -> None:
    prefix = "::error::" if github else "FAIL: "
    for problem in problems:
        print(f"{prefix}{problem}")
    print(
        f"\n{len(problems)} dependency-pinning problem(s). "
        "See docs/dependency-review.md.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
