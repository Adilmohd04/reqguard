"""Sandboxing, baseline verification, and mutant classification."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from test_integrity_agent.config import Config
from test_integrity_agent.errors import BaselineFailureError, RepositoryError, SuiteCommandError
from test_integrity_agent.models import MutantOutcome, Mutation, MutationSource
from test_integrity_agent.runner import SandboxPool, run_mutant, verify_baseline

PYTEST_CMD = f'"{sys.executable}" -m pytest -q'


def config_for(repo: Path, **overrides: object) -> Config:
    config = Config.load(repo)
    config.test_command = PYTEST_CMD
    for key, value in overrides.items():
        setattr(config, key, value)
    config.validate()
    return config


def mutation_for(repo: Path, relative: str, old: str, new: str) -> Mutation:
    original = (repo / relative).read_text(encoding="utf-8")
    assert old in original, "fixture drifted: the text to replace is gone"
    mutated = original.replace(old, new, 1)
    return Mutation(
        id="REQ-001-test",
        requirement_id="REQ-001",
        path=relative,
        original_source=original,
        mutated_source=mutated,
        explanation="test mutation",
        origin=MutationSource.AST,
        operator="test",
    )


def test_sandbox_copies_the_repo_and_excludes_junk(tiny_repo: Path) -> None:
    (tiny_repo / ".venv").mkdir()
    (tiny_repo / ".venv" / "big").write_text("x", encoding="utf-8")
    config = config_for(tiny_repo)
    with SandboxPool(config, size=2) as pool:
        sandbox = pool.first
        assert (sandbox.root / "src" / "pkg" / "account.py").is_file()
        assert not (sandbox.root / ".venv").exists()
        assert sandbox.root != tiny_repo


def test_the_working_tree_is_never_modified(tiny_repo: Path) -> None:
    source = tiny_repo / "src" / "pkg" / "account.py"
    before = source.read_text(encoding="utf-8")
    config = config_for(tiny_repo)
    mutation = mutation_for(tiny_repo, "src/pkg/account.py", "amount > balance", "amount > 10**9")
    with SandboxPool(config, size=1) as pool:
        verify_baseline(pool, config)
        run_mutant(pool, config, mutation)
    assert source.read_text(encoding="utf-8") == before


def test_a_sandbox_is_restored_between_mutants(tiny_repo: Path) -> None:
    config = config_for(tiny_repo)
    mutation = mutation_for(tiny_repo, "src/pkg/account.py", "amount > balance", "amount > 10**9")
    with SandboxPool(config, size=1) as pool:
        run_mutant(pool, config, mutation)
        sandbox = pool.first
        text = (sandbox.root / "src" / "pkg" / "account.py").read_text(encoding="utf-8")
        assert "amount > balance" in text


def test_a_mutant_the_suite_catches_is_killed(tiny_repo: Path) -> None:
    config = config_for(tiny_repo)
    # Removing the overdraft guard is caught by test_overdraft_is_rejected.
    mutation = mutation_for(tiny_repo, "src/pkg/account.py", "amount > balance", "amount > 10**9")
    with SandboxPool(config, size=1) as pool:
        verify_baseline(pool, config)
        result = run_mutant(pool, config, mutation)
    assert result.outcome is MutantOutcome.KILLED
    assert result.returncode not in (0, None)
    assert result.diff


def test_a_mutant_nothing_covers_survives(tiny_repo: Path) -> None:
    config = config_for(tiny_repo)
    # The suite never withdraws exactly the full balance, so >= is untested.
    mutation = mutation_for(tiny_repo, "src/pkg/account.py", "amount > balance", "amount >= balance")
    with SandboxPool(config, size=1) as pool:
        verify_baseline(pool, config)
        result = run_mutant(pool, config, mutation)
    assert result.outcome is MutantOutcome.SURVIVED
    assert result.survived is True


def test_a_hanging_mutant_times_out_and_is_not_a_kill(tiny_repo: Path) -> None:
    config = config_for(tiny_repo, timeout=5)
    mutation = mutation_for(
        tiny_repo,
        "src/pkg/account.py",
        "    return balance - amount",
        "    import time\n    time.sleep(120)\n    return balance - amount",
    )
    with SandboxPool(config, size=1) as pool:
        result = run_mutant(pool, config, mutation)
    assert result.outcome is MutantOutcome.TIMEOUT
    assert result.returncode is None


def test_a_red_baseline_stops_the_run(tiny_repo: Path) -> None:
    (tiny_repo / "tests" / "test_broken.py").write_text(
        "def test_fails():\n    assert False\n", encoding="utf-8"
    )
    config = config_for(tiny_repo)
    with SandboxPool(config, size=1) as pool, pytest.raises(BaselineFailureError) as exc:
        verify_baseline(pool, config)
    assert "before any mutation" in exc.value.message
    assert "green" in (exc.value.hint or "")


def test_a_suite_with_no_tests_is_a_baseline_failure(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    config = Config.load(tmp_path)
    config.test_command = PYTEST_CMD
    with SandboxPool(config, size=1) as pool, pytest.raises(BaselineFailureError) as exc:
        verify_baseline(pool, config)
    assert "collected no tests" in exc.value.message


def test_an_unrunnable_command_is_reported_clearly(tiny_repo: Path) -> None:
    config = config_for(tiny_repo, test_command="definitely-not-a-real-binary --go")
    with SandboxPool(config, size=1) as pool, pytest.raises(SuiteCommandError) as exc:
        verify_baseline(pool, config)
    assert "was not found" in exc.value.message


def test_a_mutation_path_cannot_escape_the_sandbox(tiny_repo: Path) -> None:
    config = config_for(tiny_repo)
    escape = Mutation(
        id="bad",
        requirement_id="REQ-001",
        path="../../escaped.py",
        original_source="",
        mutated_source="print('pwned')\n",
        explanation="",
        origin=MutationSource.AST,
    )
    with SandboxPool(config, size=1) as pool:
        with pytest.raises(RepositoryError, match="outside the sandbox"):
            pool.first.resolve(escape.path)
        result = run_mutant(pool, config, escape)
    assert result.outcome is MutantOutcome.ERROR
    assert not (tiny_repo.parent / "escaped.py").exists()


def test_the_api_key_is_not_handed_to_the_test_suite(
    tiny_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    config = config_for(tiny_repo)
    with SandboxPool(config, size=1) as pool:
        sandbox = pool.first
        env = sandbox.environment()
        assert "ANTHROPIC_API_KEY" not in env
        assert str(sandbox.root) in env["PYTHONPATH"]
        assert str(sandbox.root / "src") in env["PYTHONPATH"]


def test_split_command_survives_windows_paths() -> None:
    """A backslash path in test_command must not be eaten by shell escaping."""
    import os

    from test_integrity_agent.errors import split_command

    parts = split_command(r'"C:\venv\Scripts\python.exe" -m pytest -q')
    assert parts[1:] == ["-m", "pytest", "-q"]
    if os.name == "nt":
        assert parts[0] == r"C:\venv\Scripts\python.exe"


def test_split_command_handles_a_plain_command() -> None:
    from test_integrity_agent.errors import split_command

    assert split_command("pytest -x -q") == ["pytest", "-x", "-q"]
