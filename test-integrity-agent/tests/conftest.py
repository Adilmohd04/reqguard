"""Shared fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from test_integrity_agent.example import example_root


@pytest.fixture
def example_repo(tmp_path: Path) -> Path:
    """A fresh copy of the bundled example repository."""
    dest = tmp_path / "example"
    shutil.copytree(
        example_root(),
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".tia_cache"),
    )
    return dest


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A minimal repo: one rule, one implementation, one test that misses the boundary."""
    repo = tmp_path / "tiny"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()

    (repo / "REQUIREMENTS.md").write_text(
        "# Rules\n\n"
        "- REQ-001: A withdrawal must never leave the balance below zero.\n"
        "- REQ-002: The account name must never be empty.\n",
        encoding="utf-8",
    )
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "account.py").write_text(
        "def withdraw(balance, amount):\n"
        '    """Take money out of an account."""\n'
        "    if amount > balance:\n"
        "        raise ValueError('insufficient funds')\n"
        "    return balance - amount\n",
        encoding="utf-8",
    )
    (repo / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / 'src'))\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_account.py").write_text(
        "import pytest\nfrom pkg.account import withdraw\n\n\n"
        "def test_withdraw_leaves_balance():\n"
        "    assert withdraw(100, 40) == 60\n\n\n"
        "def test_overdraft_is_rejected():\n"
        "    with pytest.raises(ValueError):\n"
        "        withdraw(100, 500)\n",
        encoding="utf-8",
    )
    (repo / "tia.yaml").write_text(
        "requirements:\n  - REQUIREMENTS.md\n"
        "source_paths:\n  - src\n"
        "test_command: python -m pytest -q\n"
        "timeout: 60\n"
        "mutants_per_req: 2\n"
        "workers: 2\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture(autouse=True)
def _no_real_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this suite may reach the Anthropic API."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
