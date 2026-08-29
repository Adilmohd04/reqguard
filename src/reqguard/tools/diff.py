"""Unified-diff helpers. Owned by Track A.

Reference: docs/03-implementation-manual.md sections 6.3, 12. stdlib only.
"""

from __future__ import annotations

import difflib

__all__ = ["unified_diff", "diff_line_count"]


def unified_diff(before: str, after: str, path: str) -> str:
    """A git-style unified diff between two versions of one file."""
    text = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def diff_line_count(before: str, after: str) -> int:
    """Number of added + removed lines (hunk headers and context excluded)."""
    n = 0
    for line in difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm=""
    ):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            n += 1
    return n
