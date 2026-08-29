"""Sandbox — copy, run, teardown.

Reference: docs/03-implementation-manual.md section 7.

Default copy strategy; worktree opt-in.
git worktree is faster but checks out tracked files only — no .venv, no untracked
fixtures, no generated data. On an arbitrary user repo that silently breaks the
suite, and a broken suite reads as mutation killed: a false negative that looks
like success. Copy is the safe default.

Teardown goes in a finally: block, always. A crashed run that leaves mutated
trees in /tmp is how someone eventually runs a mutant by accident.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reqguard.config import Config

# Directories to exclude when copying the repo
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def _should_exclude(path: Path) -> bool:
    """Check if a path should be excluded from copy."""
    # Check if any part of the path is an excluded directory
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    return False


def materialize_repo(source: Path, target: Path, cfg: "Config | None" = None) -> None:
    """Materialize the repository into the sandbox using copy strategy.

    Copies the source repo to target, excluding cache/venv/git directories.
    """
    target.mkdir(parents=True, exist_ok=True)

    for src_item in source.rglob("*"):
        if _should_exclude(src_item):
            continue

        # Compute relative path from source
        try:
            rel = src_item.relative_to(source)
        except ValueError:
            continue

        dst = target / rel

        if src_item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            # Ensure parent directory exists
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_item, dst)


@contextlib.contextmanager
def sandbox(repo: Path, cfg: "Config | None" = None):
    """Context manager for a sandboxed repository copy.

    Yields the path to the sandbox directory. Teardown (removal) is guaranteed
    via finally: block.
    """
    path = Path(tempfile.mkdtemp(prefix="reqguard-"))
    try:
        materialize_repo(repo, path, cfg)
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    # Smoke test
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo = tmp / "test_repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("print('hello')")
        (repo / ".git").mkdir()
        (repo / ".git" / "config").write_text("")
        (repo / "__pycache__").mkdir()
        (repo / "__pycache__" / "main.cpython-312.pyc").write_text("")

        with sandbox(repo) as sb:
            print(f"Sandbox: {sb}")
            print(f"Exists: {sb.exists()}")
            print(f"Has src: {(sb / 'src').exists()}")
            print(f"Has .git: {(sb / '.git').exists()}")
            print(f"Has __pycache__: {(sb / '__pycache__').exists()}")
            assert (sb / "src" / "main.py").exists()
            assert not (sb / ".git").exists()
            assert not (sb / "__pycache__").exists()
        print(f"After cleanup: {sb.exists()}")