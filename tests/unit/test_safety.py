"""Tests for safe_resolve — the containment guard every tool and mutation goes
through (docs/03-implementation-manual.md section 4.2).

Acceptance criteria (crew/tasks.yaml A1):
  - blocks ../ traversal
  - blocks absolute paths outside root
  - blocks symlink escape
  - allows root itself
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from reqguard.errors import PathEscape
from reqguard.verification.safety import safe_resolve


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    r = tmp_path / "sandbox"
    r.mkdir()
    (r / "pkg").mkdir()
    (r / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return r


def test_allows_simple_relative_path(root: Path) -> None:
    assert safe_resolve(root, "pkg/mod.py") == (root / "pkg" / "mod.py").resolve()


def test_allows_root_itself(root: Path) -> None:
    assert safe_resolve(root, ".") == root.resolve()
    assert safe_resolve(root, "") == root.resolve()


def test_allows_new_file_inside_root(root: Path) -> None:
    # The file need not exist yet — containment is about location, not existence.
    assert safe_resolve(root, "pkg/new.py") == (root / "pkg" / "new.py").resolve()


def test_blocks_parent_traversal(root: Path) -> None:
    with pytest.raises(PathEscape):
        safe_resolve(root, "../outside.py")


def test_blocks_deep_parent_traversal(root: Path) -> None:
    with pytest.raises(PathEscape):
        safe_resolve(root, "pkg/../../outside.py")


def test_blocks_absolute_path_outside_root(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.py"
    with pytest.raises(PathEscape):
        safe_resolve(root, str(outside))


def test_allows_absolute_path_inside_root(root: Path) -> None:
    inside = root / "pkg" / "mod.py"
    assert safe_resolve(root, str(inside)) == inside.resolve()


def test_blocks_symlink_escape(root: Path, tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret = outside_dir / "secret.py"
    secret.write_text("s3cr3t = 1\n", encoding="utf-8")
    link = root / "escape"
    try:
        os.symlink(outside_dir, link)
    except (OSError, NotImplementedError) as exc:  # Windows without privilege
        pytest.skip(f"cannot create symlink on this host: {exc}")
    with pytest.raises(PathEscape):
        safe_resolve(root, "escape/secret.py")


def test_symlink_pointing_inside_root_is_allowed(root: Path) -> None:
    try:
        os.symlink(root / "pkg", root / "alias")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlink on this host: {exc}")
    assert safe_resolve(root, "alias/mod.py") == (root / "pkg" / "mod.py").resolve()
