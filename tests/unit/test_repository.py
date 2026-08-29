"""Tests for the seven repository read tools (docs/03-implementation-manual.md 4.1).

Acceptance (crew/tasks.yaml A2):
  - list_files, read_file, search_code, find_symbol, find_references,
    inspect_tests, git_diff
  - every path argument goes through safe_resolve
  - git_diff returns '' on a non-git dir — never raises
  - find_references docstring states it is regex-based, not scope-aware
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reqguard.errors import PathEscape
from reqguard.tools import repository


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "src" / "orders").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "src" / "orders" / "service.py").write_text(
        "class OrderService:\n"
        "    def ship_order(self, order):\n"
        '        if order.status == "cancelled":\n'
        "            raise ValueError(order.id)\n"
        "        order.status = \"shipped\"\n"
        "        return order\n",
        encoding="utf-8",
    )
    (r / "tests" / "test_orders.py").write_text(
        "def test_ship():\n    assert True\n", encoding="utf-8"
    )
    return r


def test_list_files_returns_repo_relative_posix_paths(repo: Path):
    files = repository.list_files(repo)
    assert "src/orders/service.py" in files
    assert "tests/test_orders.py" in files
    assert all(not f.startswith("/") for f in files)


def test_read_file_prefixes_line_numbers(repo: Path):
    out = repository.read_file(repo, "src/orders/service.py", 1, 2)
    assert out.splitlines()[0].startswith("L1: ")
    assert "class OrderService" in out


def test_search_code_returns_file_line_text(repo: Path):
    hits = repository.search_code(repo, r"cancelled")
    assert hits and hits[0]["file"] == "src/orders/service.py"
    assert isinstance(hits[0]["line"], int)


def test_find_symbol_reports_dotted_name_and_span(repo: Path):
    syms = repository.find_symbol(repo, "ship_order")
    assert syms and syms[0]["symbol"] == "OrderService.ship_order"
    assert syms[0]["start_line"] == 2
    assert "raise ValueError" in syms[0]["source"]


def test_find_references_is_word_boundary(repo: Path):
    refs = repository.find_references(repo, "ship_order")
    assert any(r["file"] == "src/orders/service.py" for r in refs)


def test_find_references_docstring_states_the_limitation():
    doc = repository.find_references.__doc__ or ""
    assert "NOT scope-aware" in doc or "not scope-aware" in doc.lower()
    assert "regex" in doc.lower()


def test_inspect_tests_lists_node_ids(repo: Path):
    got = repository.inspect_tests(repo)
    assert any(g["node_id"].endswith("::test_ship") for g in got)


def test_git_diff_empty_on_non_git_dir_never_raises(repo: Path):
    assert repository.git_diff(repo) == ""


def test_git_diff_shows_uncommitted_change(tmp_path: Path):
    r = tmp_path / "g"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True)
    (r / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert "x = 2" in repository.git_diff(r)


@pytest.mark.parametrize(
    "fn",
    [
        lambda repo: repository.read_file(repo, "../escape.py"),
        lambda repo: repository.read_file(repo, "src/../../escape.py"),
    ],
)
def test_path_args_go_through_safe_resolve(repo: Path, fn):
    with pytest.raises(PathEscape):
        fn(repo)


# ── review-mandated robustness tests ────────────────────────────────────────

def test_read_file_returns_empty_on_missing_file(repo: Path):
    """read_file must not crash on paths that don't exist inside the sandbox."""
    out = repository.read_file(repo, "src/nonexistent.py")
    assert out == ""


def test_read_file_returns_empty_on_undecodable_file(repo: Path, tmp_path: Path):
    """read_file must not crash on binary files that can't be decoded as UTF-8."""
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\x80\x81\x82\xff\xfe")
    # Write the binary path into the repo so safe_resolve resolves it
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "binary.bin").write_bytes(b"\x80\x81\x82\xff\xfe")
    out = repository.read_file(repo, "src/binary.bin")
    assert out == ""


def test_search_code_returns_empty_on_invalid_regex(repo: Path):
    """search_code must not propagate re.error for malformed patterns."""
    hits = repository.search_code(repo, r"\[invalid regex")
    assert hits == []


def test_search_code_limit_truncation(repo: Path):
    """search_code respects the limit parameter — returns at most `limit` hits."""
    # Write many files containing "test"
    for i in range(50):
        (repo / f"src/file{i}.py").write_text(f"x = {i}\n", encoding="utf-8")
    # Also add one in tests/ which should be skipped
    (repo / "tests" / "conftest.py").write_text("x = 999\n", encoding="utf-8")

    hits = repository.search_code(repo, r"x = \d+", limit=5)
    assert len(hits) <= 5
    # First 5 should all match
    for h in hits[:5]:
        assert "x =" in h["text"]


def test_list_files_limit_truncation(repo: Path):
    """list_files respects the limit parameter."""
    files = repository.list_files(repo, limit=10)
    assert len(files) <= 10
