"""Tool Runner bindings over Track A's repository read tools. Owned by Track B.

This module **binds**, it does not reimplement. Every function here is a thin
`@beta_tool` wrapper that calls the matching pure function in
`reqguard.tools.repository` (Track A, task A2). A second copy of the read logic
would fork the behaviour and only one copy would get tested.

Reference: docs/03-implementation-manual.md sections 4.3, 5.3.

Cache stability (section 4.3): the returned list is in a **fixed, sorted order**.
Tool definitions are part of the cached prefix; a set that reorders between calls
silently invalidates the cache and multiplies cost. `build_read_tools` sorts by
tool name before returning, and `tool_names()` lets a test assert the order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from anthropic import beta_tool

from reqguard.tools import repository

__all__ = ["build_read_tools", "tool_names", "TOOL_NAMES"]

# The seven read tools, sorted. This is the canonical order every call emits.
TOOL_NAMES: tuple[str, ...] = (
    "find_references",
    "find_symbol",
    "git_diff",
    "inspect_tests",
    "list_files",
    "read_file",
    "search_code",
)


def build_read_tools(root: Path | str) -> list[Any]:
    """Seven read-only tools bound to ``root``, sorted by name.

    The model gets read access to the sandbox and nothing else: no tool here
    writes, executes, or returns a verdict (section 10.1).
    """
    root = Path(root)

    @beta_tool
    def list_files(glob: str = "**/*.py", limit: int = 200) -> str:
        """List repository files matching a glob.

        Args:
            glob: Glob pattern, POSIX separators. Defaults to all Python files.
            limit: Maximum number of paths to return.
        """
        return json.dumps(repository.list_files(root, glob, limit))

    @beta_tool
    def read_file(path: str, start: int = 1, end: int | None = None) -> str:
        """Read a file, returning its lines with 1-indexed ``Lnn:`` prefixes.

        Args:
            path: Repo-relative path to the file.
            start: First line to return (1-indexed).
            end: Last line to return (1-indexed); omit for end of file.
        """
        return repository.read_file(root, path, start, end)

    @beta_tool
    def search_code(pattern: str, glob: str = "**/*.py", limit: int = 50) -> str:
        """Search the repository with a Python regular expression.

        Args:
            pattern: Python regular expression.
            glob: Restrict the search to files matching this glob.
            limit: Maximum number of matches to return.
        """
        return json.dumps(repository.search_code(root, pattern, glob, limit))

    @beta_tool
    def find_symbol(name: str) -> str:
        """Find a function or class definition by name (AST walk).

        Args:
            name: The exact function or class name to locate.
        """
        return json.dumps(repository.find_symbol(root, name))

    @beta_tool
    def find_references(name: str) -> str:
        """Find where a symbol is used. WORD-BOUNDARY REGEX, NOT scope-aware —
        results include comments, strings, and unrelated same-named symbols.
        Treat every hit as a candidate to verify by reading, not as a fact.

        Args:
            name: The identifier to search for.
        """
        return json.dumps(repository.find_references(root, name))

    @beta_tool
    def inspect_tests() -> str:
        """List the project's collected pytest node ids."""
        return json.dumps(repository.inspect_tests(root))

    @beta_tool
    def git_diff(ref: str = "HEAD") -> str:
        """Return ``git diff`` against a ref, or an empty string on a non-git dir.

        Args:
            ref: The git ref to diff against. Defaults to ``HEAD``.
        """
        return repository.git_diff(root, ref)

    by_name: dict[str, Callable[..., Any]] = {
        "list_files": list_files,
        "read_file": read_file,
        "search_code": search_code,
        "find_symbol": find_symbol,
        "find_references": find_references,
        "inspect_tests": inspect_tests,
        "git_diff": git_diff,
    }
    return [by_name[n] for n in TOOL_NAMES]


def tool_names(tools: list[Any]) -> list[str]:
    """The tool names in the order they appear — for a cache-stability assertion."""
    names: list[str] = []
    for t in tools:
        name = getattr(t, "name", None)
        if name is None and isinstance(t, dict):
            name = t.get("name")
        names.append(name or repr(t))
    return names
