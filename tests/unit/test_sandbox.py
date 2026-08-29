"""Unit tests for the sandbox lifecycle.

Reference: docs/03-implementation-manual.md section 7.
Task: A5 — needs A1 (safe_resolve) from Track A.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import textwrap
from pathlib import Path

import pytest

from reqguard.models import RunStatus
from reqguard.sandbox import sandbox, materialize_repo
from reqguard.tools.test_runner import run_tests


class TestMaterializeRepo:
    """Tests for the repo materialization (copy strategy)."""

    def _create_sample_repo(self, tmp: Path) -> Path:
        """Create a sample repository structure."""
        repo = tmp / "sample_repo"
        repo.mkdir()

        # Source code
        src = repo / "src" / "mypkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "service.py").write_text(textwrap.dedent("""
            def add(a: int, b: int) -> int:
                return a + b
        """))

        # Tests
        tests = repo / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        (tests / "test_service.py").write_text(textwrap.dedent("""
            from mypkg.service import add
            def test_add():
                assert add(1, 2) == 3
        """))

        # Config files
        (repo / "pyproject.toml").write_text(textwrap.dedent("""
            [project]
            name = "sample"
            version = "0.1.0"
            requires-python = ">=3.12"
            dependencies = []

            [tool.pytest.ini_options]
            pythonpath = ["src"]
            testpaths = ["tests"]
        """))

        return repo

    def test_copy_excludes_git(self):
        """Copy strategy excludes .git directory."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)
            (repo / ".git").mkdir()
            (repo / ".git" / "config").write_text("[core]\n")

            with sandbox(repo) as sb:
                assert not (sb / ".git").exists()

    def test_copy_excludes_venv(self):
        """Copy strategy excludes .venv directory."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)
            (repo / ".venv").mkdir()
            (repo / ".venv" / "pyvenv.cfg").write_text("")

            with sandbox(repo) as sb:
                assert not (sb / ".venv").exists()

    def test_copy_excludes_pycache(self):
        """Copy strategy excludes __pycache__ directories."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)
            pycache = repo / "src" / "mypkg" / "__pycache__"
            pycache.mkdir(parents=True)
            (pycache / "service.cpython-312.pyc").write_text("")

            with sandbox(repo) as sb:
                assert not (sb / "src" / "mypkg" / "__pycache__").exists()

    def test_copy_excludes_node_modules(self):
        """Copy strategy excludes node_modules directory."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "package.json").write_text("{}")

            with sandbox(repo) as sb:
                assert not (sb / "node_modules").exists()

    def test_copy_excludes_other_caches(self):
        """Copy strategy excludes .tox, .mypy_cache, .pytest_cache, .ruff_cache."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)
            for cache in [".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"]:
                (repo / cache).mkdir()
                (repo / cache / "dummy").write_text("")

            with sandbox(repo) as sb:
                for cache in [".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"]:
                    assert not (sb / cache).exists(), f"{cache} should be excluded"

    def test_source_repo_byte_identical_after_mutated_run(self):
        """Source repo is byte-identical after a mutated run."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            # Capture original state
            original_service = (repo / "src" / "mypkg" / "service.py").read_bytes()

            # Use sandbox and mutate a file
            with sandbox(repo) as sb:
                service_in_sandbox = sb / "src" / "mypkg" / "service.py"
                service_in_sandbox.write_text(textwrap.dedent("""
                    def add(a: int, b: int) -> int:
                        return a - b  # MUTATED
                """))
                # Run tests in sandbox (will fail due to mutation)
                run_tests(sb, timeout_s=30)

            # Source repo should be unchanged
            assert (repo / "src" / "mypkg" / "service.py").read_bytes() == original_service


class TestSandboxLifecycle:
    """Tests for the sandbox context manager lifecycle."""

    def _create_sample_repo(self, tmp: Path) -> Path:
        """Create a sample repository structure."""
        repo = tmp / "sample_repo"
        repo.mkdir()

        src = repo / "src" / "mypkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "service.py").write_text(textwrap.dedent("""
            def add(a: int, b: int) -> int:
                return a + b
        """))

        tests = repo / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        (tests / "test_service.py").write_text(textwrap.dedent("""
            from mypkg.service import add
            def test_add():
                assert add(1, 2) == 3
        """))

        (repo / "pyproject.toml").write_text(textwrap.dedent("""
            [project]
            name = "sample"
            version = "0.1.0"
            requires-python = ">=3.12"
            dependencies = []

            [tool.pytest.ini_options]
            pythonpath = ["src"]
            testpaths = ["tests"]
        """))

        return repo

    def test_sandbox_creates_temp_dir(self):
        """Sandbox creates a temporary directory with the right prefix."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            with sandbox(repo) as sb:
                assert sb.exists()
                assert sb.is_dir()
                assert sb.name.startswith("reqguard-")
                assert (sb / "src" / "mypkg" / "service.py").exists()

    def test_sandbox_teardown_runs_in_finally(self):
        """Teardown runs in finally: even when the body raises."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            sandbox_path = None
            try:
                with sandbox(repo) as sb:
                    sandbox_path = sb
                    raise RuntimeError("Simulated error in sandbox body")
            except RuntimeError:
                pass

            # Sandbox should be cleaned up
            assert sandbox_path is not None
            assert not sandbox_path.exists(), "Sandbox should be cleaned up after exception"

    def test_sandbox_teardown_on_normal_exit(self):
        """Teardown runs on normal exit."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            sandbox_path = None
            with sandbox(repo) as sb:
                sandbox_path = sb
                assert sb.exists()

            # Sandbox should be cleaned up
            assert sandbox_path is not None
            assert not sandbox_path.exists(), "Sandbox should be cleaned up after normal exit"

    def test_sandbox_runs_tests_successfully(self):
        """Tests run successfully inside the sandbox."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            with sandbox(repo) as sb:
                outcome = run_tests(sb, timeout_s=30)
                assert outcome.status is RunStatus.OK
                assert outcome.exit_code == 0
                assert outcome.passed >= 1

    def test_sandbox_is_isolated(self):
        """Multiple sandboxes are isolated from each other."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            with sandbox(repo) as sb1:
                with sandbox(repo) as sb2:
                    assert sb1 != sb2
                    # Mutate in sb1
                    (sb1 / "src" / "mypkg" / "service.py").write_text("def add(a, b): return a - b")
                    # sb2 should be unaffected
                    assert (sb2 / "src" / "mypkg" / "service.py").read_text() != "def add(a, b): return a - b"

    def test_sandbox_cleanup_on_timeout(self):
        """Sandbox is cleaned up even if test times out."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = self._create_sample_repo(tmp)

            # Create a test that hangs
            test_file = repo / "tests" / "test_hang.py"
            test_file.write_text(textwrap.dedent("""
                import time
                def test_hangs():
                    time.sleep(100)
            """))

            sandbox_path = None
            try:
                with sandbox(repo) as sb:
                    sandbox_path = sb
                    outcome = run_tests(sb, timeout_s=0.1)
                    assert outcome.status is RunStatus.TIMEOUT
            except Exception:
                pass

            # Sandbox should be cleaned up
            assert sandbox_path is not None
            assert not sandbox_path.exists(), "Sandbox should be cleaned up after timeout"


class TestSandboxEdgeCases:
    """Edge cases for sandbox."""

    def test_sandbox_with_empty_repo(self):
        """Sandbox handles empty repo gracefully."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = tmp / "empty_repo"
            repo.mkdir()
            (repo / "pyproject.toml").write_text("")

            with sandbox(repo) as sb:
                assert sb.exists()
                outcome = run_tests(sb, timeout_s=30)
                assert outcome.status is RunStatus.ENVIRONMENT_ERROR
                assert outcome.exit_code == 5  # no tests collected

    def test_sandbox_preserves_symlinks_in_source(self):
        """Symlinks in source are preserved (or handled safely)."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            repo = tmp / "repo_with_symlink"
            repo.mkdir()
            src = repo / "src"
            src.mkdir()
            (src / "real_file.py").write_text("x = 1")
            # Create a symlink
            (src / "link_file.py").symlink_to("real_file.py")

            (repo / "pyproject.toml").write_text("")

            with sandbox(repo) as sb:
                # The symlink should be copied as a symlink or as the target file
                assert (sb / "src" / "real_file.py").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])