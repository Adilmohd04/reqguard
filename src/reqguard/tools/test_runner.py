"""Test runner with exit-code taxonomy.

Reference: docs/03-implementation-manual.md sections 8.1, 8.2.

Only pytest exit 0 and 1 yield a survival verdict. Everything else is
ENVIRONMENT_ERROR. A missing dependency makes pytest exit 4; read naively as
"tests failed" that scores as a mutation kill and the kill rate rises for free.

This module uses stdlib only — no external dependencies.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from reqguard.models import RunStatus, TestOutcome

if TYPE_CHECKING:
    from reqguard.config import Config


# Default test command — can be overridden by config
# Use sys.executable -m pytest to ensure we use the current interpreter's pytest
DEFAULT_TEST_COMMAND = f"{sys.executable} -m pytest"


def classify_exit_code(exit_code: int, timed_out: bool) -> tuple[RunStatus, str | None]:
    """Classify a pytest exit code into a RunStatus.

    Only exit 0 and 1 yield RunStatus.OK. Everything else is ENVIRONMENT_ERROR.
    Timeout yields TIMEOUT.

    Reference: Blueprint §33, Doc 3 section 8.2.
    """
    if timed_out:
        return RunStatus.TIMEOUT, "suite exceeded timeout"

    match exit_code:
        case 0:
            return RunStatus.OK, None          # all passed
        case 1:
            return RunStatus.OK, None          # tests failed — VALID run
        case 2:
            return RunStatus.ENVIRONMENT_ERROR, "interrupted"
        case 3:
            return RunStatus.ENVIRONMENT_ERROR, "internal pytest error"
        case 4:
            return RunStatus.ENVIRONMENT_ERROR, "pytest usage error"
        case 5:
            return RunStatus.ENVIRONMENT_ERROR, "no tests collected"
        case _:
            return RunStatus.ENVIRONMENT_ERROR, f"exit {exit_code}"


def _try_parse_json_report(report_path: Path) -> dict | None:
    """Try to parse a pytest-json-report file. Returns None if unavailable or invalid."""
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _extract_outcome_from_json(data: dict) -> TestOutcome:
    """Extract TestOutcome from pytest-json-report data."""
    summary = data.get("summary", {})
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    errors = summary.get("error", 0)
    skipped = summary.get("skipped", 0)
    duration = summary.get("duration", 0.0)

    failing_node_ids: list[str] = []
    for test in data.get("tests", []):
        if test.get("outcome") in ("failed", "error"):
            failing_node_ids.append(test.get("nodeid", ""))

    return TestOutcome(
        status=RunStatus.OK,  # Will be overridden by classify_exit_code
        exit_code=0,          # Will be overridden
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration_seconds=duration,
        failing_node_ids=failing_node_ids,
        stdout_tail="",
    )


def _extract_outcome_from_stdout(stderr: str, stdout: str, exit_code: int) -> TestOutcome:
    """Fallback: extract minimal outcome from stdout/stderr when JSON report unavailable."""
    # Try to parse the last few lines for passed/failed counts
    # pytest output format: "X passed, Y failed, Z errors, W skipped in T.TTs"
    import re

    combined = stdout + "\n" + stderr
    passed = failed = errors = skipped = 0

    # Pattern: "N passed, M failed, K errors, L skipped"
    pattern = r"(\d+)\s+(passed|failed|error|skipped)"
    for match in re.finditer(pattern, combined):
        count = int(match.group(1))
        kind = match.group(2)
        if kind == "passed":
            passed = count
        elif kind == "failed":
            failed = count
        elif kind == "error":
            errors = count
        elif kind == "skipped":
            skipped = count

    return TestOutcome(
        status=RunStatus.OK,  # Will be overridden
        exit_code=exit_code,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration_seconds=0.0,
        failing_node_ids=[],
        stdout_tail=combined[-2000:] if combined else "",
    )


def run_tests(
    sandbox: Path,
    test_command: str = DEFAULT_TEST_COMMAND,
    timeout_s: float = 300.0,
) -> TestOutcome:
    """Run the test suite in the sandbox and return a TestOutcome.

    Uses pytest with --json-report when available, falls back to exit-code-only
    parsing. Uses start_new_session=True so timeout can kill the whole process group.
    """
    # Prepare the command with JSON report output
    #
    # shlex.split() in its default POSIX mode treats backslash as an escape
    # character. test_command is frequently DEFAULT_TEST_COMMAND, i.e.
    # f"{sys.executable} -m pytest" — and sys.executable on Windows is an
    # absolute path full of backslashes (C:\Users\...\python.exe). POSIX-mode
    # splitting silently eats every one of them, producing a mangled path
    # that CreateProcess can't find ([WinError 2]), which subprocess.run
    # then reports as FileNotFoundError -> misclassified as exit 2
    # (ENVIRONMENT_ERROR) rather than actually running the suite. Non-POSIX
    # mode leaves backslashes alone; it is the correct mode on every platform
    # for a command string that was never shell-quoted to begin with.
    report_file = sandbox / ".pytest_report.json"
    cmd_parts = shlex.split(test_command, posix=(os.name != "nt"))
    cmd_parts.extend(["-s", "--json-report", f"--json-report-file={report_file}"])

    # Environment: ensure we can find pytest
    env = os.environ.copy()
    # Add sandbox to PYTHONPATH so local modules are importable
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(sandbox) + (os.pathsep + pythonpath if pythonpath else "")

    # Config.testing.command defaults to the bare string "pytest", which only
    # resolves via PATH -- true in an activated venv, false whenever the
    # caller invokes an interpreter directly (`.venv/Scripts/python.exe ...`,
    # many CI setups, an IDE terminal that never sourced activate). When that
    # happens CreateProcess/execvp can't find it, subprocess.run raises
    # FileNotFoundError, and the run is silently misclassified as
    # ENVIRONMENT_ERROR before pytest ever starts -- the single case this
    # module exists to prevent (see the module docstring), just one layer
    # further out. Rescue only the exact bare-"pytest" case (never a custom
    # multi-word command a user configured on purpose, and never an
    # already-qualified interpreter path) by substituting the interpreter
    # that is actually running this process.
    if cmd_parts and cmd_parts[0] == "pytest" and shutil.which("pytest", path=env.get("PATH")) is None:
        cmd_parts = [sys.executable, "-m", "pytest", *cmd_parts[1:]]

    timed_out = False
    start_time = time.perf_counter()
    exit_code = 0
    stdout = ""
    stderr = ""

    try:
        proc = subprocess.run(
            cmd_parts,
            cwd=sandbox,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            start_new_session=True,  # Critical: allows killpg on timeout
            env=env,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except OSError as e:
        # Sandbox directory doesn't exist, or the interpreter/pytest wasn't
        # found. The concrete exception varies by platform for the same
        # condition: POSIX raises FileNotFoundError for a missing cwd;
        # Windows raises NotADirectoryError ([WinError 267]) for the same
        # case and FileNotFoundError ([WinError 2]) for a missing
        # executable. Both are OSError subclasses and neither overlaps
        # subprocess.TimeoutExpired (a SubprocessError), so one broad
        # except here covers every OS's spelling of "couldn't even start."
        exit_code = 2
        stderr = str(e)
        stdout = ""
    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = -1
        stdout = e.stdout or ""
        stderr = e.stderr or ""
        # The process group was started with start_new_session=True,
        # so the OS already killed it when the timeout fired.
        # No need for manual killpg here — subprocess.run handles it.

    duration = time.perf_counter() - start_time

    # Classify the exit code
    status, reason = classify_exit_code(exit_code, timed_out)

    # Try to parse JSON report first (rich data)
    json_data = _try_parse_json_report(report_file)
    if json_data is not None:
        outcome = _extract_outcome_from_json(json_data)
        outcome.status = status
        outcome.exit_code = exit_code
        outcome.duration_seconds = duration
        outcome.reason = reason
        # stdout_tail from JSON report if available
        if not outcome.stdout_tail:
            outcome.stdout_tail = (stdout + stderr)[-2000:]
        return outcome

    # Fallback: parse stdout/stderr (degraded, not broken)
    outcome = _extract_outcome_from_stdout(stderr, stdout, exit_code)
    outcome.status = status
    outcome.exit_code = exit_code
    outcome.duration_seconds = duration
    outcome.reason = reason
    return outcome


# Convenience function for config-based invocation
def run_tests_with_config(sandbox: Path, cfg: "Config") -> TestOutcome:
    """Run tests using configuration from Config object."""
    return run_tests(
        sandbox=sandbox,
        test_command=cfg.testing.command,
        timeout_s=cfg.testing.timeout_seconds,
    )


if __name__ == "__main__":
    # Smoke test: run on current directory
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "test_smoke.py").write_text("def test_ok(): assert True\n")
        result = run_tests(tmp, timeout_s=30)
        print(f"Status: {result.status}")
        print(f"Exit code: {result.exit_code}")
        print(f"Passed: {result.passed}, Failed: {result.failed}")
        print(f"Duration: {result.duration_seconds:.3f}s")