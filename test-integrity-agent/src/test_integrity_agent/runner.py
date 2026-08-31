"""Apply mutants in isolated copies of the repository and run the test suite.

The working tree is never touched. Every mutant is written into a sandbox -- a
copy of the repository made once per worker -- and the file is restored from the
pristine text afterwards, so a sandbox is reusable across mutants without the
copy cost being paid per mutant.

Two failure modes get first-class handling because both silently corrupt
results otherwise:

* **A red baseline.** If the suite fails before any mutation, a failing mutant
  proves nothing, so the run stops with :class:`BaselineFailureError`.
* **Imports resolving outside the sandbox.** If the package under test is
  installed (editable or otherwise) pointing at the original tree, the tests
  would import unmutated code and every mutant would "survive". The sandbox and
  its source roots are prepended to ``PYTHONPATH`` to win that race, and a
  100%-survival run is flagged in the report as suspicious.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .errors import (
    BaselineFailureError,
    RepositoryError,
    SuiteCommandError,
    split_command,
)
from .logging_setup import get_logger
from .models import MutantOutcome, MutantResult, Mutation
from .mutator import make_diff

_log = get_logger("runner")

OUTPUT_TAIL_LINES = 40
PYTEST_NO_TESTS_COLLECTED = 5


@dataclass
class RunOutcome:
    """The raw result of one test-suite execution."""

    returncode: int | None
    output: str
    duration_s: float
    timed_out: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0

    @property
    def tail(self) -> str:
        lines = self.output.strip().splitlines()
        return "\n".join(lines[-OUTPUT_TAIL_LINES:])


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    """Kill a test process and everything it spawned, on any platform."""
    if process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
            os.killpg(os.getpgid(process.pid), 9)
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        try:
            process.kill()
        except OSError:
            pass


class Sandbox:
    """One disposable copy of the repository."""

    def __init__(self, root: Path, config: Config) -> None:
        self.root = root
        self.config = config
        self._pristine: dict[str, str] = {}

    def resolve(self, relative_path: str) -> Path:
        """Resolve a repo-relative path inside the sandbox, refusing escapes."""
        target = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root != target and root not in target.parents:
            raise RepositoryError(
                f"Refusing to write outside the sandbox: {relative_path}"
            )
        return target

    def apply(self, mutation: Mutation) -> None:
        """Write the mutated file, remembering the original text for restore."""
        target = self.resolve(mutation.path)
        if mutation.path not in self._pristine:
            self._pristine[mutation.path] = target.read_text(encoding="utf-8")
        target.write_text(mutation.mutated_source, encoding="utf-8", newline="")

    def restore(self) -> None:
        """Put every mutated file back to its original text."""
        for relative_path, text in self._pristine.items():
            try:
                self.resolve(relative_path).write_text(text, encoding="utf-8", newline="")
            except OSError as exc:  # pragma: no cover - disk failure
                _log.error(
                    "could not restore sandbox file",
                    extra={"context": {"path": relative_path, "error": str(exc)}},
                )
        self._pristine.clear()

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        # The sandbox must win over any installed copy of the package.
        entries = [str(self.root)]
        for source_path in self.config.source_paths:
            candidate = self.root / source_path
            if candidate.is_dir():
                entries.append(str(candidate))
        existing = env.get("PYTHONPATH", "")
        if existing:
            entries.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(entries)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["TIA_SANDBOX"] = "1"
        # Never hand an API key to someone else's test suite.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        return env

    def run_tests(self, command: str, timeout: int) -> RunOutcome:
        """Run ``command`` in this sandbox, capturing output with a hard timeout."""
        argv = split_command(command)
        started = time.monotonic()
        # Put the test process in its own group/session so a timeout can kill
        # the whole tree, not just the launcher.
        isolation: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )

        try:
            process = subprocess.Popen(
                argv,
                cwd=str(self.root),
                env=self.environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                **isolation,
            )
        except FileNotFoundError as exc:
            raise SuiteCommandError(
                f"Could not run the test command: {argv[0]!r} was not found.",
                hint=(
                    "Set `test_command` in tia.yaml (or pass --test-command) to "
                    "something runnable from the repository root, for example "
                    '"python -m pytest -q".'
                ),
            ) from exc
        except OSError as exc:
            raise SuiteCommandError(f"Could not run the test command: {exc}") from exc

        try:
            output, _ = process.communicate(timeout=timeout)
            return RunOutcome(
                returncode=process.returncode,
                output=output or "",
                duration_s=time.monotonic() - started,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            _terminate_tree(process)
            try:
                output, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
                output = ""
            return RunOutcome(
                returncode=None,
                output=output or "",
                duration_s=time.monotonic() - started,
                timed_out=True,
            )


class SandboxPool:
    """A fixed set of sandboxes, one per worker, handed out under a lock."""

    def __init__(self, config: Config, size: int) -> None:
        self.config = config
        self.size = max(1, size)
        self._tempdir: Path | None = None
        self._available: queue.Queue[Sandbox] = queue.Queue()
        self._sandboxes: list[Sandbox] = []
        self._lock = threading.Lock()

    def __enter__(self) -> SandboxPool:
        self.setup()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()

    def _ignore(self, directory: str, entries: list[str]) -> set[str]:
        excluded = self.config.excluded
        return {entry for entry in entries if entry in excluded}

    def setup(self) -> None:
        """Copy the repository ``size`` times into a temporary directory."""
        base = Path(tempfile.mkdtemp(prefix="tia-"))
        self._tempdir = base
        source = self.config.repo
        _log.debug(
            "creating sandboxes",
            extra={"context": {"count": self.size, "base": str(base)}},
        )
        for index in range(self.size):
            target = base / f"sandbox-{index}"
            try:
                shutil.copytree(source, target, ignore=self._ignore, symlinks=True)
            except OSError as exc:
                self.cleanup()
                raise RepositoryError(
                    f"Could not copy the repository into a sandbox: {exc}",
                    hint=(
                        "Check free disk space and that no file in the repository "
                        "is locked by another process."
                    ),
                ) from exc
            sandbox = Sandbox(target, self.config)
            self._sandboxes.append(sandbox)
            self._available.put(sandbox)

    def acquire(self, timeout: float | None = None) -> Sandbox:
        return self._available.get(timeout=timeout)

    def release(self, sandbox: Sandbox) -> None:
        sandbox.restore()
        self._available.put(sandbox)

    def cleanup(self) -> None:
        if self._tempdir is None:
            return
        shutil.rmtree(self._tempdir, ignore_errors=True)
        self._tempdir = None
        self._sandboxes.clear()

    @property
    def first(self) -> Sandbox:
        if not self._sandboxes:
            raise RepositoryError("Sandbox pool has not been set up.")
        return self._sandboxes[0]


def verify_baseline(pool: SandboxPool, config: Config) -> RunOutcome:
    """Run the unmutated suite once. Raises unless it passes and ran something."""
    sandbox = pool.first
    timeout = max(config.timeout, 60)
    _log.debug(
        "running baseline",
        extra={"context": {"command": config.test_command, "timeout": timeout}},
    )
    outcome = sandbox.run_tests(config.test_command, timeout)
    if outcome.timed_out:
        raise BaselineFailureError(
            config.test_command,
            f"The suite did not finish within {timeout}s.",
        )
    if outcome.returncode == PYTEST_NO_TESTS_COLLECTED:
        raise BaselineFailureError(
            config.test_command,
            "pytest collected no tests (exit code 5). There is no suite to check.",
        )
    if not outcome.passed:
        raise BaselineFailureError(config.test_command, outcome.output)
    return outcome


def run_mutant(pool: SandboxPool, config: Config, mutation: Mutation) -> MutantResult:
    """Apply one mutant in a free sandbox and classify the result."""
    started = time.monotonic()
    sandbox = pool.acquire()
    try:
        try:
            sandbox.apply(mutation)
        except (OSError, RepositoryError) as exc:
            return MutantResult(
                mutation=mutation,
                outcome=MutantOutcome.ERROR,
                duration_s=time.monotonic() - started,
                error=f"could not apply the mutant: {exc}",
                diff=make_diff(mutation),
            )

        outcome = sandbox.run_tests(config.test_command, config.timeout)
        if outcome.timed_out:
            classification = MutantOutcome.TIMEOUT
        elif outcome.returncode == PYTEST_NO_TESTS_COLLECTED:
            classification = MutantOutcome.ERROR
        elif outcome.passed:
            classification = MutantOutcome.SURVIVED
        else:
            classification = MutantOutcome.KILLED

        _log.debug(
            "mutant evaluated",
            extra={
                "context": {
                    "mutant": mutation.id,
                    "requirement": mutation.requirement_id,
                    "outcome": classification.value,
                    "seconds": round(outcome.duration_s, 2),
                }
            },
        )
        return MutantResult(
            mutation=mutation,
            outcome=classification,
            duration_s=outcome.duration_s,
            returncode=outcome.returncode,
            output_tail=outcome.tail,
            diff=make_diff(mutation),
            error=(
                "the test command collected no tests against this mutant"
                if classification is MutantOutcome.ERROR
                else ""
            ),
        )
    finally:
        pool.release(sandbox)
