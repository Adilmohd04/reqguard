"""Unit tests for the verifier two-condition gate.

Reference: docs/03-implementation-manual.md section 9.
Task: A6 — needs A4 + A5 (both done).
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import pytest

from reqguard.models import RunStatus, TestOutcome, Verification
from reqguard.sandbox import sandbox
from reqguard.tools.test_runner import run_tests
from reqguard.verification.verifier import verify


def _create_test_repo(tmp: Path, test_content: str) -> Path:
    """Create a minimal repo with a test file.

    Module-level so every test class shares one implementation. `tmp` may be a
    nested path that does not yet exist (e.g. ``<tmpdir>/mutant``); all
    directories are created with ``parents=True`` so a nested base never raises
    FileNotFoundError.
    """
    repo = tmp / "test_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "main.py").write_text("def add(a, b): return a + b\n")
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_main.py").write_text(test_content)
    (repo / "pyproject.toml").write_text(textwrap.dedent("""
        [project]
        name = "test"
        version = "0.1.0"
        requires-python = ">=3.12"
        dependencies = []

        [tool.pytest.ini_options]
        pythonpath = ["src"]
        testpaths = ["tests"]
    """))
    return repo


class TestVerifier:
    """Tests for the verify function."""

    def test_verify_passes_on_original_and_kills_mutant(self):
        """Valid test: passes on original, fails on mutant -> verified=True."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            # Original repo: test passes
            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))

            # Mutant repo: test fails (mutation changes add to subtract)
            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    # Verify the repair test
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    assert result.passes_on_original is True
                    assert result.kills_mutant is True
                    assert result.verified is True
                    assert result.failure_reason is None

    def test_verify_assert_false_rejected(self):
        """assert False test kills mutant but fails on original -> rejected."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                from main import add
                def test_add():
                    assert False  # Always fails
            """))

            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_add():
                    assert False  # Always fails
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    # assert False fails on original (exit 1), so passes_on_original=False
                    assert result.passes_on_original is False
                    assert result.kills_mutant is True  # Also fails on mutant
                    assert result.verified is False
                    assert result.failure_reason is not None
                    assert "passes_on_original" in result.failure_reason.lower() or "failed on original" in result.failure_reason.lower()

    def test_verify_test_passes_everywhere_rejected(self):
        """Test that passes everywhere (e.g., assert True) -> rejected."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                from main import add
                def test_always_passes():
                    assert True  # Always passes
            """))

            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_always_passes():
                    assert True  # Always passes
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    # assert True passes on both, so kills_mutant=False
                    assert result.passes_on_original is True
                    assert result.kills_mutant is False
                    assert result.verified is False
                    assert result.failure_reason is not None
                    assert "kills_mutant" in result.failure_reason.lower() or "did not kill" in result.failure_reason.lower()

    def test_verify_environment_error_on_original(self):
        """ENVIRONMENT_ERROR on original run -> verified=False, reason recorded."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            # Original repo with import error
            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                import nonexistent_module_xyz
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))

            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    assert result.passes_on_original is False
                    assert result.verified is False
                    assert result.failure_reason is not None
                    assert "environment" in result.failure_reason.lower()

    def test_verify_environment_error_on_mutant(self):
        """ENVIRONMENT_ERROR on mutant run -> verified=False, reason recorded."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))

            # Mutant repo whose SOURCE is unimportable. The verifier runs the
            # SAME repair test (read from the original tree) against both
            # sandboxes, so an environment error on the mutant must come from
            # the mutant's own source/environment — not from a different test
            # file. A syntax error in main.py makes `from main import add`
            # raise on import -> pytest collection error -> exit 2 ->
            # ENVIRONMENT_ERROR, on the mutant run only.
            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b # noqa\ndef broken(:\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    assert result.kills_mutant is False
                    assert result.verified is False
                    assert result.failure_reason is not None
                    assert "environment" in result.failure_reason.lower()

    def test_verify_timeout_on_original(self):
        """TIMEOUT on original run -> verified=False."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            # Original repo with hanging test
            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                import time
                def test_hangs():
                    time.sleep(100)
            """))

            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb, timeout_s=0.1)

                    assert result.passes_on_original is False
                    assert result.verified is False
                    assert result.failure_reason is not None
                    assert "timeout" in result.failure_reason.lower()

    def test_verify_both_environment_error(self):
        """ENVIRONMENT_ERROR on both runs -> verified=False."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                import nonexistent_module_xyz
                def test_foo():
                    assert True
            """))

            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                import another_nonexistent_module_abc
                def test_foo():
                    assert True
            """))

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    assert result.verified is False
                    assert result.failure_reason is not None


class TestVerifierEdgeCases:
    """Edge cases for verifier."""

    def test_verify_nonexistent_test_file(self):
        """Verifying a nonexistent test file should handle gracefully."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            original_repo = tmp / "orig"
            original_repo.mkdir()
            (original_repo / "src").mkdir()
            (original_repo / "src" / "main.py").write_text("def add(a, b): return a + b\n")
            (original_repo / "tests").mkdir()
            (original_repo / "tests" / "test_main.py").write_text("def test_add(): assert True\n")
            (original_repo / "pyproject.toml").write_text("")

            mutant_repo = tmp / "mutant"
            mutant_repo.mkdir()
            (mutant_repo / "src").mkdir()
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")
            (mutant_repo / "tests").mkdir()
            (mutant_repo / "tests" / "test_main.py").write_text("def test_add(): assert True\n")
            (mutant_repo / "pyproject.toml").write_text("")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    nonexistent_path = orig_sb / "tests" / "nonexistent.py"
                    result = verify(nonexistent_path, orig_sb, mut_sb)

                    assert result.verified is False
                    assert result.failure_reason is not None

    def test_verify_test_with_syntax_error(self):
        """Test file with syntax error -> ENVIRONMENT_ERROR."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            original_repo = _create_test_repo(tmp, textwrap.dedent("""
                def test_syntax_error(:
                    assert True
            """))

            mutant_repo = _create_test_repo(tmp / "mutant", textwrap.dedent("""
                from main import add
                def test_add():
                    assert add(1, 2) == 3
            """))
            (mutant_repo / "src" / "main.py").write_text("def add(a, b): return a - b\n")

            with sandbox(original_repo) as orig_sb:
                with sandbox(mutant_repo) as mut_sb:
                    repair_test_path = orig_sb / "tests" / "test_main.py"
                    result = verify(repair_test_path, orig_sb, mut_sb)

                    assert result.passes_on_original is False
                    assert result.verified is False
                    assert result.failure_reason is not None
                    assert "environment" in result.failure_reason.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])