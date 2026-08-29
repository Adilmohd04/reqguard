"""Regression test for crewctl.working_tree_files() untracked expansion.

Bug: `git status --porcelain` collapses a wholly-untracked directory to a
single `dir/` entry. Exact ownership patterns can't match the owned files
inside it, so `guard` reports `owned by ?` and `stage` omits them. The fix
passes `--untracked-files=all` so every untracked file is listed individually.

This test builds a throwaway git repo containing a wholly-untracked nested
directory and proves working_tree_files() returns the individual file paths,
never the collapsed directory entry.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

CREWCTL = Path(__file__).resolve().parent.parent / "crewctl.py"


def _load_crewctl():
    spec = importlib.util.spec_from_file_location("crewctl_under_test", CREWCTL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    # Deterministic identity so `git status` behaves the same everywhere.
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    # Seed one committed file so HEAD exists; the untracked dir is what matters.
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")
    return repo


def test_untracked_nested_files_are_expanded_not_collapsed(tmp_path, monkeypatch):
    crewctl = _load_crewctl()
    repo = _init_repo(tmp_path)

    # A wholly-untracked nested directory: this is exactly the case git
    # collapses to `pkg/` under the default --untracked-files=normal.
    nested = repo / "pkg" / "sub"
    nested.mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("a\n", encoding="utf-8")
    (nested / "b.py").write_text("b\n", encoding="utf-8")

    monkeypatch.setattr(crewctl, "ROOT", repo)
    files = crewctl.working_tree_files()

    # The collapsed directory entry must never appear.
    assert "pkg/" not in files
    # Every individual owned file must be visible for ownership matching.
    assert "pkg/a.py" in files
    assert "pkg/sub/b.py" in files


def test_quoted_filename_with_spaces_is_unquoted(tmp_path, monkeypatch):
    crewctl = _load_crewctl()
    repo = _init_repo(tmp_path)

    # git quotes paths containing spaces in porcelain output; the parser strips
    # the surrounding double quotes. Prove an untracked file with a space in a
    # wholly-untracked dir is both expanded and unquoted.
    nested = repo / "pkg"
    nested.mkdir()
    (nested / "my file.py").write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(crewctl, "ROOT", repo)
    files = crewctl.working_tree_files()

    assert "pkg/" not in files
    assert "pkg/my file.py" in files
    # No stray surrounding quotes leaked through.
    assert not any(f.startswith('"') or f.endswith('"') for f in files)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
