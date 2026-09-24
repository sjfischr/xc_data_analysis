"""Validate relative Markdown links across the repository (spec Task 1.5).

Checks every tracked ``*.md`` file (docs, spec, skills, reports) for:

* inline links/images ``[text](target)`` and reference definitions
  ``[label]: target`` whose targets are relative paths — the target file must
  exist;
* fragment links into Markdown files (``doc.md#section`` or ``#section``) —
  the heading anchor must exist in the target file (GitHub slug rules).

External ``http(s)``/``mailto`` links are intentionally not fetched: ordinary
CI performs static checks only and never depends on live endpoints.

Usage:
    python scripts/check_markdown_links.py [--github]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
}

# [text](target "title") — target captured up to closing paren (no nesting).
_INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)<>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")
# [label]: target
_REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s+<?(\S+)>?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def github_slug(heading: str) -> str:
    """Approximate GitHub's heading-to-anchor slug algorithm."""
    text = re.sub(r"`([^`]*)`", r"\1", heading)  # unwrap inline code
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # unwrap links
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        seen = counts.get(slug, 0)
        counts[slug] = seen + 1
        anchors.add(slug if seen == 0 else f"{slug}-{seen}")
    return anchors


def iter_markdown_files() -> list[Path]:
    files = []
    for path in sorted(REPO_ROOT.rglob("*.md")):
        if not any(part in EXCLUDED_DIRS for part in path.parts):
            files.append(path)
    return files


def iter_links(path: Path) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Strip inline code spans so example links are ignored.
        stripped = re.sub(r"`[^`]*`", "", line)
        for match in _INLINE_LINK_RE.finditer(stripped):
            links.append((lineno, match.group(1)))
        ref = _REF_DEF_RE.match(stripped)
        if ref:
            links.append((lineno, ref.group(1)))
    return links


def check_file(path: Path, anchor_cache: dict[Path, set[str]]) -> list[str]:
    errors: list[str] = []
    for lineno, raw_target in iter_links(path):
        parts = urlsplit(raw_target)
        if parts.scheme or parts.netloc:
            continue  # external link: not validated in static CI
        target_path = unquote(parts.path)
        fragment = parts.fragment

        if target_path:
            resolved = (path.parent / target_path).resolve()
            if not resolved.exists():
                errors.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: "
                    f"broken relative link '{raw_target}'"
                )
                continue
        else:
            resolved = path

        if fragment and resolved.suffix.lower() == ".md" and resolved.is_file():
            anchors = anchor_cache.setdefault(resolved, heading_anchors(resolved))
            if fragment.lower() not in anchors:
                errors.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: "
                    f"missing anchor '#{fragment}' in "
                    f"'{resolved.relative_to(REPO_ROOT).as_posix()}'"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--github",
        action="store_true",
        help="emit GitHub Actions ::error annotations",
    )
    args = parser.parse_args()

    anchor_cache: dict[Path, set[str]] = {}
    all_errors: list[str] = []
    files = iter_markdown_files()
    for path in files:
        all_errors.extend(check_file(path, anchor_cache))

    for error in all_errors:
        if args.github:
            location, message = error.split(": ", 1)
            file_part, line_part = location.rsplit(":", 1)
            print(f"::error file={file_part},line={line_part}::{message}")
        else:
            print(error)

    status = "FAILED" if all_errors else "OK"
    print(
        f"markdown link check {status}: {len(files)} files scanned, "
        f"{len(all_errors)} broken link(s)."
    )
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
