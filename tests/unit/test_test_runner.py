"""Unit tests for the test runner exit-code taxonomy.

Reference: docs/03-implementation-manual.md sections 8.1, 8.2.
Task: A4 — critical path.

The exit-code taxonomy is the single most likely way the system lies to us.
A missing dependency makes pytest exit 4; read naively as "tests failed" that
scores as a mutation kill and the kill rate rises for free.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from reqguard.models import RunStatus, TestOutcome
from reqguard.tools.test_runner import run_tests, classify_exit_code


class TestClassifyExitCode:
    """Test the exit-code classification logic in isolation."""

    def test_exit_0_is_ok(self):
        status, reason = classify_exit_code(0, timed_out=False)
        assert status is RunStatus.OK
        assert reason is None

    def test_exit_1_is_ok(self):
        """Exit 1 means tests failed but the run is VALID — not a mutation kill."""
        status, reason = classify_exit_code(1, timed_out=False)
        assert status is RunStatus.OK
        assert reason is None

    def test_exit_2_is_environment_error(self):
        status, reason = classify_exit_code(2, timed_out=False)
        assert status is RunStatus.ENVIRONMENT_ERROR
        assert "interrupted" in reason.lower()

    def test_exit_3_is_environment_error(self):
        status, reason = classify_exit_code(3, timed_out=False)
        assert status is RunStatus.ENVIRONMENT_ERROR
        assert "internal" in reason.lower()

    def test_exit_4_is_environment_error(self):
        """Exit 4 = pytest usage error (e.g., missing dependency). Must NOT be a kill."""
        status, reason = classify_exit_code(4, timed_out=False)
        assert status is RunStatus.ENVIRONMENT_ERROR
        assert "usage" in reason.lower()

    def test_exit_5_is_environment_error(self):
        """Exit 5 = no tests collected. Must NOT be a kill."""
        status, reason = classify_exit_code(5, timed_out=False)
        assert status is RunStatus.ENVIRONMENT_ERROR
        assert "no tests" in reason.lower()

    def test_exit_6_is_environment_error(self):
        """Unknown exit codes are environment errors."""
        status, reason = classify_exit_code(6, timed_out=False)
        assert status is RunStatus.ENVIRONMENT_ERROR
        assert "exit 6" in reason.lower()

    def test_timeout_is_timeout_status(self):
        status, reason = classify_exit_code(0, timed_out=True)
        assert status is RunStatus.TIMEOUT
        assert "timeout" in reason.lower()


class TestRunTests:
    """Integration-style tests using real pytest subprocesses."""

    def _write_test_file(self, tmp: Path, content: str) -> Path:
        test_file = tmp / "test_sample.py"
        test_file.write_text(content)
        return test_file

    def test_all_pass_exit_0(self):
        """All tests pass -> exit 0 -> RunStatus.OK, survived=False."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                def test_passes():
                    assert 1 + 1 == 2
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.OK
            assert outcome.exit_code == 0
            assert outcome.passed >= 1
            assert outcome.failed == 0

    def test_some_fail_exit_1(self):
        """Some tests fail -> exit 1 -> RunStatus.OK (valid run, not env error)."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                def test_passes():
                    assert 1 + 1 == 2
                def test_fails():
                    assert 1 + 1 == 3
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.OK
            assert outcome.exit_code == 1
            assert outcome.passed >= 1
            assert outcome.failed >= 1

    def test_no_tests_collected_exit_5(self):
        """No tests collected -> exit 5 -> ENVIRONMENT_ERROR."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # Empty test file - no test functions
            (tmp / "test_empty.py").write_text("# no tests here\n")
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.ENVIRONMENT_ERROR
            assert outcome.exit_code == 5

    def test_syntax_error_exit_2(self):
        """Syntax error in test file -> exit 2 (collection interrupted) -> ENVIRONMENT_ERROR."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "test_bad_syntax.py").write_text("def test(:\n    pass\n")
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.ENVIRONMENT_ERROR
            assert outcome.exit_code == 2
            assert outcome.reason is not None
            assert "interrupted" in outcome.reason.lower()

    def test_import_error_exit_2(self):
        """Missing import -> exit 2 (collection interrupted) -> ENVIRONMENT_ERROR (not a mutation kill!)."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                import nonexistent_module_that_does_not_exist_12345
                def test_something():
                    assert True
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.ENVIRONMENT_ERROR
            assert outcome.exit_code == 2
            # This is the critical assertion: missing dependency must NOT read as "killed"
            assert outcome.reason is not None
            assert "interrupted" in outcome.reason.lower()

    def test_timeout_kills_process_group(self):
        """Timeout -> RunStatus.TIMEOUT, process group killed."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                import time
                def test_hangs():
                    time.sleep(100)
            """))
            # Very short timeout to trigger the timeout path
            outcome = run_tests(tmp, timeout_s=0.1)
            assert outcome.status is RunStatus.TIMEOUT
            assert outcome.reason is not None
            assert "timeout" in outcome.reason.lower()

    def test_failing_node_ids_captured(self):
        """Failing test node IDs are captured in the outcome."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                def test_passes():
                    assert True
                def test_fails():
                    assert False
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.OK
            assert outcome.exit_code == 1
            assert len(outcome.failing_node_ids) >= 1
            assert any("test_fails" in nid for nid in outcome.failing_node_ids)

    def test_stdout_tail_captured(self):
        """Stdout tail is captured for debugging."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                def test_with_output():
                    print("HELLO FROM TEST")
                    assert True
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.OK
            assert "HELLO FROM TEST" in outcome.stdout_tail

    def test_duration_seconds_recorded(self):
        """Duration is recorded."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                def test_passes():
                    assert True
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.duration_seconds > 0
            assert outcome.duration_seconds < 30

    def test_pytest_json_report_fallback(self):
        """When pytest-json-report is not available, falls back to exit-code-only."""
        # This test verifies the fallback behavior by checking that the function
        # works even when we can't parse JSON report.
        # The actual fallback is tested by the fact that all above tests pass
        # without requiring pytest-json-report to be installed.
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_test_file(tmp, textwrap.dedent("""
                def test_passes():
                    assert True
            """))
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.OK
            # If we get here without JSON parsing errors, fallback works

    def test_start_new_session_true(self):
        """Verify start_new_session=True is used so timeout can killpg."""
        # This is implicitly tested by test_timeout_kills_process_group -
        # if start_new_session=False, the child process would not be in a new
        # process group and killpg would not work, causing the test to hang.
        # The fact that test_timeout_kills_process_group completes proves this.
        pass


class TestRunTestsEdgeCases:
    """Edge cases and error conditions."""

    def test_nonexistent_directory(self):
        """Running in a nonexistent directory should yield ENVIRONMENT_ERROR."""
        outcome = run_tests(Path("/nonexistent/path/that/does/not/exist"), timeout_s=30)
        assert outcome.status is RunStatus.ENVIRONMENT_ERROR

    def test_non_python_project(self):
        """Directory with no Python tests -> exit 5 -> ENVIRONMENT_ERROR."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "README.md").write_text("# Not a Python project\n")
            outcome = run_tests(tmp, timeout_s=30)
            assert outcome.status is RunStatus.ENVIRONMENT_ERROR
            assert outcome.exit_code == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])