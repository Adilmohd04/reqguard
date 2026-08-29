"""Verifier — the two-condition gate.

Reference: docs/03-implementation-manual.md section 9.

Both conditions are load-bearing:
- kills_mutant alone is satisfied by `assert False`, which kills every mutant and repairs nothing.
- passes_on_original alone proves the test is valid but not that it detects anything.

verified = passes_on_original AND kills_mutant
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from reqguard.models import RunStatus, Verification

if TYPE_CHECKING:
    from reqguard.config import Config


def _run_single_test(
    sandbox: Path,
    test_source: str,
    timeout_s: float = 30.0,
) -> tuple[RunStatus, int, str]:
    """Run the repair test *inside* ``sandbox`` and return (status, exit_code, reason).

    The repair test source is materialized into ``sandbox`` itself before
    running, and pytest is invoked with ``sandbox`` as both cwd and rootdir
    anchor. This is load-bearing: if the test file physically lives in a
    different tree, pytest infers its rootdir (and therefore any
    ``[tool.pytest.ini_options] pythonpath``) from *that* tree, so
    ``import`` statements resolve against the wrong source and a mutant can
    silently survive (exit 0). Running the test from within the target
    sandbox guarantees ``import`` resolves against that sandbox's own source.

    A per-run unique filename avoids colliding with an existing test module of
    the same name already present in the sandbox tree.
    """
    from reqguard.tools.test_runner import classify_exit_code

    tests_dir = sandbox / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    # Unique name so we never shadow or get shadowed by an existing test module,
    # and so a stale file from a prior run in the same tree cannot be picked up.
    test_file = tests_dir / f"test_reqguard_verify_{uuid.uuid4().hex}.py"
    test_file.write_text(test_source, encoding="utf-8")

    # Anchor rootdir at the sandbox and put its src on the path, so imports
    # resolve against THIS tree's source (original vs mutant).
    src_dir = sandbox / "src"
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    prefix = str(src_dir) if src_dir.is_dir() else str(sandbox)
    env["PYTHONPATH"] = prefix + (os.pathsep + pythonpath if pythonpath else "")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_file),
        "-x",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(sandbox),
        "--tb=short",
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=sandbox,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            start_new_session=True,
            env=env,
        )
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        exit_code = -1
    finally:
        # Never leave the injected test behind in the tree.
        with contextlib.suppress(OSError):
            test_file.unlink()

    status, reason = classify_exit_code(exit_code, timed_out)
    return status, exit_code, reason


def verify(
    repair_test_path: Path,
    original_sandbox: Path,
    mutant_sandbox: Path,
    timeout_s: float = 30.0,
) -> Verification:
    """Verify a repair test satisfies both conditions:
    A. passes_on_original: test passes against the original (correct) implementation
    B. kills_mutant: test fails when the mutation is applied

    Returns a Verification object with the results.
    """
    # Read the repair test source once. A missing or unreadable file is an
    # environment problem, not a repair signal: it must never be scored as a
    # kill. Surface it as verified=False with a recorded reason.
    try:
        test_source = repair_test_path.read_text(encoding="utf-8")
    except OSError as exc:
        return Verification(
            passes_on_original=False,
            kills_mutant=False,
            verified=False,
            failure_reason=f"environment error: cannot read repair test ({exc})",
        )

    # Condition A: run test on original code - must PASS (exit 0)
    status_a, exit_a, reason_a = _run_single_test(original_sandbox, test_source, timeout_s)
    passes_on_original = (status_a is RunStatus.OK and exit_a == 0)

    # Condition B: run test on mutant code - must FAIL (exit 1)
    status_b, exit_b, reason_b = _run_single_test(mutant_sandbox, test_source, timeout_s)
    kills_mutant = (status_b is RunStatus.OK and exit_b == 1)

    # Both conditions must be true
    verified = passes_on_original and kills_mutant

    # Build failure reason if not verified
    failure_reason = None
    if not verified:
        reasons = []
        if not passes_on_original:
            if status_a is RunStatus.ENVIRONMENT_ERROR:
                reasons.append(f"environment error on original: {reason_a}")
            elif status_a is RunStatus.TIMEOUT:
                reasons.append("timeout on original")
            else:
                reasons.append(f"test failed on original (exit {exit_a})")
        if not kills_mutant:
            if status_b is RunStatus.ENVIRONMENT_ERROR:
                reasons.append(f"environment error on mutant: {reason_b}")
            elif status_b is RunStatus.TIMEOUT:
                reasons.append("timeout on mutant")
            else:
                reasons.append(f"test did not kill mutant (exit {exit_b})")
        failure_reason = "; ".join(reasons)

    return Verification(
        passes_on_original=passes_on_original,
        kills_mutant=kills_mutant,
        verified=verified,
        failure_reason=failure_reason,
    )


if __name__ == "__main__":
    # Smoke test
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        # Original
        orig = tmp / "orig"
        orig.mkdir()
        (orig / "src").mkdir()
        (orig / "src" / "main.py").write_text("def add(a, b): return a + b\n")
        (orig / "tests").mkdir()
        test_file = orig / "tests" / "test_main.py"
        test_file.write_text("from main import add\ndef test_add(): assert add(1, 2) == 3\n")
        (orig / "pyproject.toml").write_text("")

        # Mutant
        mut = tmp / "mut"
        mut.mkdir()
        (mut / "src").mkdir()
        (mut / "src" / "main.py").write_text("def add(a, b): return a - b\n")
        (mut / "tests").mkdir()
        (mut / "tests" / "test_main.py").write_text("from main import add\ndef test_add(): assert add(1, 2) == 3\n")
        (mut / "pyproject.toml").write_text("")

        from reqguard.sandbox import sandbox
        with sandbox(orig) as o_sb:
            with sandbox(mut) as m_sb:
                result = verify(o_sb / "tests" / "test_main.py", o_sb, m_sb)
                print(f"passes_on_original: {result.passes_on_original}")
                print(f"kills_mutant: {result.kills_mutant}")
                print(f"verified: {result.verified}")
                print(f"failure_reason: {result.failure_reason}")