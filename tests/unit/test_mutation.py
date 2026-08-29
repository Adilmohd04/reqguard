"""Tests for the mutation engine — apply + 6 gates (crew/tasks.yaml A3).

Acceptance criteria (crew/tasks.yaml A3):
  - anchor_unique: rejects 0 matches and rejects >1 matches
  - parses: rejects a mutant that fails ast.parse
  - not_a_test_file: REJECTS test_*.py, *_test.py, conftest.py, anything under tests/
  - in_sandbox: rejects a target outside the sandbox root
  - small_diff: rejects a diff over max_diff_lines
  - not_a_noop: rejects original == mutated
  - returns a unified diff on success
"""
from __future__ import annotations

import pytest

from reqguard.errors import MutationRejected
from reqguard.models import MutationSpec, MutationType
from reqguard.tools.mutation import apply_with_gates, GATES, is_test_path


def _make_spec(
    target_file: str,
    original_snippet: str,
    mutated_snippet: str,
    *,
    mutation_type: MutationType = MutationType.COMPARISON_CHANGE,
    rationale: str = "",
) -> MutationSpec:
    return MutationSpec(
        id="test-id",
        requirement_id="REQ-001",
        mutation_type=mutation_type,
        target_file=target_file,
        target_symbol="test_symbol",
        original_snippet=original_snippet,
        mutated_snippet=mutated_snippet,
        description="test",
        expected_violation="test",
        violation_scenario="test",
        rationale=rationale,
    )


def _sandbox_with_file(tmp_path, rel_path: str, content: str):
    """Create a file at rel_path inside tmp_path and return the full path."""
    full = tmp_path / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


# ── anchor_unique: 0 or >1 matches rejected ─────────────────────────────────

def test_anchor_unique_rejects_0_matches(tmp_path: Path):
    """0 matches → anchor_not_found."""
    # Create the target file with different content so the snippet isn't there
    _sandbox_with_file(tmp_path, "src/service.py", "x = 99\ndef foo(): pass\n")
    spec = _make_spec("src/service.py", "x = 100", "x = 200")
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "anchor_unique"


def test_anchor_unique_rejects_gt1_matches(tmp_path: Path):
    """>1 matches → anchor_ambiguous: the snippet appears multiple times."""
    source = "x = 1\n\ndef foo():\n    x = 1\n    return x\n"
    _sandbox_with_file(tmp_path, "src/service.py", source)
    spec = _make_spec("src/service.py", "x = 1", "x = 2")
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "anchor_unique"


# ── parses: mutated source must ast.parse ─────────────────────────────────────

def test_parses_rejects_syntax_broken_mutant(tmp_path: Path):
    """Mutated source that doesn't parse → parses rejection."""
    _sandbox_with_file(tmp_path, "src/service.py", "def foo():\n    return 42\n")
    spec = _make_spec("src/service.py", "return 42", "def foo():")
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "parses"


def test_parses_passes_valid_mutant(tmp_path: Path):
    """Mutated source that parses → success (returns unified diff)."""
    _sandbox_with_file(tmp_path, "src/service.py", "def foo():\n    return 42\n")
    spec = _make_spec("src/service.py", "return 42", "return 43")
    result = apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert isinstance(result, str)
    assert "---" in result or "+++" in result  # unified diff markers


# ── not_a_test_file: must reject test files ───────────────────────────────────

@pytest.mark.parametrize(
    "test_path",
    [
        "tests/test_foo.py",
        "test_foo.py",
        "conftest.py",
        "some/tests/bar.py",
    ],
)
def test_not_a_test_file_rejects_test(test_path: str, tmp_path: Path):
    """Test-file paths are always rejected, regardless of sandbox location."""
    # Even if the path is inside sandbox, test files are forbidden
    _sandbox_with_file(tmp_path, test_path, "def test_foo(): pass\n")
    spec = _make_spec(test_path, "def test_foo(): pass\n", "def test_foo(): pass\n")
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "not_a_test_file"


# ── in_sandbox: rejects a target outside the sandbox root ─────────────────────

def test_in_sandbox_rejects_outside(tmp_path: Path):
    """A path that escapes the sandbox root is rejected."""
    # Create a real file inside the sandbox
    _sandbox_with_file(tmp_path, "src/service.py", "x = 1\n")
    # Use a path that escapes the sandbox
    spec = _make_spec("../outside.py", "x = 1", "x = 2")
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "in_sandbox"


# ── small_diff: rejects a diff over max_diff_lines ───────────────────────────

def test_small_diff_rejects_large_diff(tmp_path: Path):
    """A diff exceeding max_diff_lines is rejected."""
    # Create the target file
    _sandbox_with_file(tmp_path, "src/service.py", "\n".join(f"line{i}" for i in range(20)))
    # A diff of 20 lines when max is 15
    long_original = "\n".join(f"line{i}" for i in range(20))
    long_mutated = "\n".join(f"line{i}_mut" for i in range(20))
    spec = _make_spec("src/service.py", long_original, long_mutated)
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "small_diff"


def test_small_diff_passes_small_diff(tmp_path: Path):
    """A diff within max_diff_lines succeeds."""
    _sandbox_with_file(tmp_path, "src/service.py", "x = 1\n")
    spec = _make_spec("src/service.py", "x = 1\n", "x = 2\n")
    result = apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert isinstance(result, str)
    assert "---" in result or "+++" in result


# ── not_a_noop: rejects original == mutated ──────────────────────────────────

def test_not_a_noop_rejects_identical(tmp_path: Path):
    """Identical original and mutated → rejected as noop."""
    _sandbox_with_file(tmp_path, "src/service.py", "x = 1\n")
    spec = _make_spec("src/service.py", "x = 1", "x = 1")
    with pytest.raises(MutationRejected) as exc:
        apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert exc.value.gate == "not_a_noop"


def test_not_a_noop_passes_when_different(tmp_path: Path):
    """Different original and mutated → success."""
    _sandbox_with_file(tmp_path, "src/service.py", "x = 1\n")
    spec = _make_spec("src/service.py", "x = 1", "x = 2")
    result = apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert isinstance(result, str)
    assert "---" in result or "+++" in result


# ── returning unified diff on success ─────────────────────────────────────────

def test_returns_unified_diff_on_success(tmp_path: Path):
    """A valid mutation returns a unified diff string."""
    _sandbox_with_file(tmp_path, "src/service.py", "x = 1\ndef foo():\n    return x\n")
    spec = _make_spec("src/service.py", "return x", "return x + 1")
    result = apply_with_gates(spec, str(tmp_path), max_diff_lines=15)
    assert isinstance(result, str)
    assert "---" in result or "+++" in result  # has unified diff markers


# ── GATES constant is correct ─────────────────────────────────────────────────

def test_gates_constant_has_six_gates():
    """GATES list has exactly 6 gates matching the acceptance criteria."""
    assert len(GATES) == 6
    gate_names = [g.name for g in GATES]
    assert "anchor_unique" in gate_names
    assert "parses" in gate_names
    assert "not_a_test_file" in gate_names
    assert "in_sandbox" in gate_names
    assert "small_diff" in gate_names
    assert "not_a_noop" in gate_names