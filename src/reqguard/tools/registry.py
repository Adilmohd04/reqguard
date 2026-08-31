"""OpenAI-compatible function-calling bindings over Track A's repository read
tools. Owned by Track B.

This module **binds**, it does not reimplement. Every function here is a thin
wrapper that calls the matching pure function in `reqguard.tools.repository`
(Track A, task A2). A second copy of the read logic would fork the behaviour
and only one copy would get tested.

Reference: docs/03-implementation-manual.md sections 4.3, 5.3.

**Migration note (COLLAB.md):** Anthropic (`@beta_tool`-decorated) ->
Gemini (bare callables, automatic dispatch) -> now OpenAI-compatible
(OpenRouter/Conduit/aerolink, `openai` SDK). Unlike Gemini's automatic
function calling, the `openai` SDK does NOT introspect callables or execute
tools itself -- it wants an explicit JSON Schema per tool
(`{"type":"function","function":{...}}`) and returns `tool_calls` for the
caller to run. So each tool here is now a `{"schema": <dict>, "impl": <fn>}`
pair: `_SCHEMAS` below is the hand-written JSON Schema sent to the model,
`by_name` maps a tool name to the actual callable, and `client.py`'s
`run_with_tools` drives the loop -- call, dispatch matched `impl`s locally,
feed results back as `role:"tool"` messages, repeat. Keep `_SCHEMAS`'
descriptions in sync with the docstrings below by hand; nothing derives one
from the other anymore.

Cache stability (section 4.3): the returned list is in a **fixed, sorted
order**. Originally written for Anthropic's explicit `cache_control` prefix
caching, where a reordered tool set silently invalidated the cache -- not
load-bearing for cost the same way on the current OpenAI-compatible surface,
but kept anyway: a stable, deterministic tool order is still worth having for
its own sake (reproducible prompts, an assertable invariant in tests).
`build_read_tools` sorts by tool name before returning, and `tool_names()`
lets a test assert the order.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

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

    def list_files(glob: str = "**/*.py", limit: int = 200) -> str:
        """List repository files matching a glob.

        Args:
            glob: Glob pattern, POSIX separators. Defaults to all Python files.
            limit: Maximum number of paths to return.
        """
        return json.dumps(repository.list_files(root, glob, limit))

    def read_file(path: str, start: int = 1, end: int | None = None) -> str:
        """Read a file, returning its lines with 1-indexed ``Lnn:`` prefixes.

        Args:
            path: Repo-relative path to the file.
            start: First line to return (1-indexed).
            end: Last line to return (1-indexed); omit for end of file.
        """
        return repository.read_file(root, path, start, end)

    def search_code(pattern: str, glob: str = "**/*.py", limit: int = 50) -> str:
        """Search the repository with a Python regular expression.

        Args:
            pattern: Python regular expression.
            glob: Restrict the search to files matching this glob.
            limit: Maximum number of matches to return.
        """
        return json.dumps(repository.search_code(root, pattern, glob, limit))

    def find_symbol(name: str) -> str:
        """Find a function or class definition by name (AST walk).

        Args:
            name: The exact function or class name to locate.
        """
        return json.dumps(repository.find_symbol(root, name))

    def find_references(name: str) -> str:
        """Find where a symbol is used. WORD-BOUNDARY REGEX, NOT scope-aware —
        results include comments, strings, and unrelated same-named symbols.
        Treat every hit as a candidate to verify by reading, not as a fact.

        Args:
            name: The identifier to search for.
        """
        return json.dumps(repository.find_references(root, name))

    def inspect_tests() -> str:
        """List the project's collected pytest node ids."""
        return json.dumps(repository.inspect_tests(root))

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

    # OpenAI/OpenRouter function schemas, one per tool, in the canonical order.
    # Each entry is {"schema": <openai tool dict>, "impl": <callable>} — the
    # client's run_with_tools reads `schema` to send and `impl` to execute.
    return [{"schema": _SCHEMAS[n], "impl": by_name[n]} for n in TOOL_NAMES]


# ── OpenAI/OpenRouter function schemas ──────────────────────────────────────
# Hand-written (not decorator-introspected) so the JSON Schema is explicit and
# stable. Descriptions mirror the docstrings above; keep them in sync.
_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_files": {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List repository files matching a glob.",
            "parameters": {
                "type": "object",
                "properties": {
                    "glob": {"type": "string", "description": "Glob pattern, POSIX separators."},
                    "limit": {"type": "integer", "description": "Max paths to return."},
                },
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file, returning lines with 1-indexed Lnn: prefixes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative path."},
                    "start": {"type": "integer", "description": "First line (1-indexed)."},
                    "end": {"type": "integer", "description": "Last line (1-indexed)."},
                },
                "required": ["path"],
            },
        },
    },
    "search_code": {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search the repository with a Python regular expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regular expression."},
                    "glob": {"type": "string", "description": "Restrict to files matching this glob."},
                    "limit": {"type": "integer", "description": "Max matches to return."},
                },
                "required": ["pattern"],
            },
        },
    },
    "find_symbol": {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Find a function or class definition by name (AST walk).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Exact function or class name."},
                },
                "required": ["name"],
            },
        },
    },
    "find_references": {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "Find where a symbol is used. WORD-BOUNDARY REGEX, NOT scope-aware "
                "— results include comments, strings, and unrelated same-named "
                "symbols. Treat every hit as a candidate to verify by reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The identifier to search for."},
                },
                "required": ["name"],
            },
        },
    },
    "inspect_tests": {
        "type": "function",
        "function": {
            "name": "inspect_tests",
            "description": "List the project's collected pytest node ids.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "git_diff": {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Return git diff against a ref, or '' on a non-git dir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "The git ref to diff against."},
                },
            },
        },
    },
}


def tool_names(tools: list[Any]) -> list[str]:
    """The tool names in the order they appear — for an order-stability
    assertion. Accepts the current OpenAI form ({"schema": {...}, "impl": fn}),
    and older forms (plain callables with __name__, Anthropic .name objects,
    or bare schema dicts) so an old fixture or test double doesn't break."""
    names: list[str] = []
    for t in tools:
        name = None
        if isinstance(t, dict):
            if "schema" in t:
                name = t["schema"].get("function", {}).get("name")
            else:
                name = t.get("name") or t.get("function", {}).get("name")
        if name is None:
            name = getattr(t, "name", None) or getattr(t, "__name__", None)
        names.append(name or repr(t))
    return names
