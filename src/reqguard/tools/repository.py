"""The seven repository read tools. Owned by Track A.

Pure functions with **no Anthropic import** — that is what lets the Tool Runner
binding (``tools/registry.py``) and a future MCP adapter stay thin bindings
rather than rewrites.

Reference: docs/03-implementation-manual.md section 4.1.

Every path argument is resolved through
:func:`reqguard.verification.safety.safe_resolve`.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from reqguard.verification.safety import safe_resolve

__all__ = [
    "list_files",
    "read_file",
    "search_code",
    "find_symbol",
    "find_references",
    "inspect_tests",
    "git_diff",
]

_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


def _skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def list_files(root: Path, glob: str = "**/*.py", limit: int = 200) -> list[str]:
    """Repo-relative paths (POSIX separators) matching ``glob``, sorted."""
    root = safe_resolve(root, ".")
    out: list[str] = []
    for p in sorted(root.glob(glob)):
        if p.is_file() and not _skip(p.relative_to(root)):
            out.append(p.relative_to(root).as_posix())
            if len(out) >= limit:
                break
    return out


def read_file(root: Path, path: str, start: int = 1, end: int | None = None) -> str:
    """File contents with 1-indexed ``Lnn:`` line-number prefixes."""
    target = safe_resolve(root, path)
    lines = target.read_text(encoding="utf-8").splitlines()
    start = max(1, start)
    end = len(lines) if end is None else min(end, len(lines))
    if start > end:
        return ""
    width = len(str(end))
    return "\n".join(f"L{i:>{width}}: {lines[i - 1]}" for i in range(start, end + 1))


def search_code(
    root: Path, pattern: str, glob: str = "**/*.py", limit: int = 50
) -> list[dict]:
    """Regex search over ``glob`` files. Returns ``[{file, line, text}]``."""
    root = safe_resolve(root, ".")
    rx = re.compile(pattern)
    hits: list[dict] = []
    for p in sorted(root.glob(glob)):
        if not p.is_file() or _skip(p.relative_to(root)):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                hits.append(
                    {"file": p.relative_to(root).as_posix(), "line": n, "text": line.strip()}
                )
                if len(hits) >= limit:
                    return hits
    return hits


def find_symbol(root: Path, name: str) -> list[dict]:
    """AST walk for a function/class definition named ``name``.

    Returns ``[{file, symbol, start_line, end_line, source}]``; ``symbol`` is
    dotted when the definition is nested (``OrderService.ship_order``).
    """
    root = safe_resolve(root, ".")
    out: list[dict] = []
    for p in sorted(root.rglob("*.py")):
        if _skip(p.relative_to(root)):
            continue
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        enclosing: dict[int, str] = {}
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef):
                for child in ast.iter_child_nodes(parent):
                    enclosing[id(child)] = parent.name
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name != name:
                continue
            prefix = enclosing.get(id(node))
            dotted = f"{prefix}.{node.name}" if prefix else node.name
            end = getattr(node, "end_lineno", node.lineno)
            out.append(
                {
                    "file": p.relative_to(root).as_posix(),
                    "symbol": dotted,
                    "start_line": node.lineno,
                    "end_line": end,
                    "source": "\n".join(lines[node.lineno - 1 : end]),
                }
            )
    return out


def find_references(root: Path, name: str) -> list[dict]:
    """Where ``name`` is used. **Word-boundary regex — NOT scope-aware.**

    Results are candidates to verify by reading, not facts: this matches the
    bare identifier anywhere, including comments, strings, and unrelated
    same-named symbols in other modules.
    """
    return search_code(root, rf"\b{re.escape(name)}\b")


def inspect_tests(root: Path) -> list[dict]:
    """``pytest --collect-only -q``. Returns ``[{node_id, file, name}]``.

    Falls back to an AST scan for ``test_*`` functions when pytest cannot
    collect — degraded, not broken.
    """
    root = safe_resolve(root, ".")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        collected: list[dict] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if "::" not in line or line.lower().startswith(("warning", "error", "=")):
                continue
            file_part = line.split("::", 1)[0]
            collected.append(
                {"node_id": line, "file": file_part, "name": line.rsplit("::", 1)[-1]}
            )
        if collected:
            return collected
    except (OSError, subprocess.SubprocessError):
        pass

    out: list[dict] = []
    for p in sorted(root.rglob("test_*.py")):
        if _skip(p.relative_to(root)):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = p.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                out.append({"node_id": f"{rel}::{node.name}", "file": rel, "name": node.name})
    return out


def git_diff(root: Path, ref: str = "HEAD") -> str:
    """``git diff`` against ``ref``. Empty string on a non-git dir — never raises."""
    try:
        proc = subprocess.run(
            ["git", "diff", ref],
            cwd=Path(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""
