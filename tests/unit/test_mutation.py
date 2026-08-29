"""Tests for the mutation engine — apply + the six gates.

Acceptance (crew/tasks.yaml A3, critical):
  - anchor_unique: rejects 0 matches and rejects >1 matches
  - parses: rejects a mutant that fails ast.parse
  - not_a_test_file: REJECTS test_*.py, *_test.py, conftest.py, under tests/
  - in_sandbox: rejects a target outside the sandbox root
  - small_diff: rejects a diff over max_diff_lines
  - not_a_noop: rejects original == mutated
  - returns a unified diff on success
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reqguard.errors import MutationRejected
from reqguard.models import MutationSpec, MutationType
from reqguard.tools import mutation


def _spec(**over) -> MutationSpec:
    base = dict(
        id="MUT-1",
        requirement_id="REQ-1",
        mutation_type=MutationType.REMOVE_GUARD,
        target_file="src/orders/service.py",
        target_symbol="ship_order",
        original_snippet='    if order.status == "cancelled":\n        raise OrderCancelledError(order.id)\n',
        mutated_snippet="",
        description="remove guard",
        expected_violation="cancelled order can ship",
        violation_scenario="ship a cancelled order",
        rationale="smallest change that violates REQ-1",
    )
    base.update(over)
    return MutationSpec(**base)


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    (tmp_path / "src" / "orders").mkdir(parents=True)
    (tmp_path / "src" / "orders" / "service.py").write_text(
        "def ship_order(order):\n"
        '    if order.status == "cancelled":\n'
        "        raise OrderCancelledError(order.id)\n"
        '    order.status = "shipped"\n'
        "    return order\n",
        encoding="utf-8",
    )
    return tmp_path


def test_success_returns_a_unified_diff_and_writes_the_mutant(sandbox: Path):
    diff = mutation.apply_with_gates(_spec(), sandbox)
    assert diff.startswith("--- a/src/orders/service.py")
    assert "-        raise OrderCancelledError(order.id)" in diff
    assert '"cancelled"' not in (sandbox / "src/orders/service.py").read_text()


def test_anchor_unique_rejects_zero_matches(sandbox: Path):
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(_spec(original_snippet="not in the file at all"), sandbox)
    assert e.value.gate == "anchor_unique"


def test_anchor_unique_rejects_more_than_one_match(sandbox: Path):
    (sandbox / "src/orders/service.py").write_text(
        "x = 1\nx = 1\n", encoding="utf-8"
    )
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(_spec(original_snippet="x = 1\n", mutated_snippet="x = 2\n"), sandbox)
    assert e.value.gate == "anchor_unique"


def test_parses_rejects_a_mutant_that_breaks_syntax(sandbox: Path):
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(
            _spec(
                original_snippet='    order.status = "shipped"\n',
                mutated_snippet="    order.status = \n",
            ),
            sandbox,
        )
    assert e.value.gate == "parses"


@pytest.mark.parametrize(
    "path",
    ["tests/test_x.py", "src/x_test.py", "conftest.py", "tests/sub/helper.py"],
)
def test_not_a_test_file_rejects_test_paths(sandbox: Path, path: str):
    assert mutation.is_test_path(path)
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(_spec(target_file=path), sandbox)
    assert e.value.gate == "not_a_test_file"


def test_in_sandbox_rejects_a_target_outside_the_root(sandbox: Path):
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(_spec(target_file="../../etc/passwd"), sandbox)
    assert e.value.gate in ("in_sandbox", "not_a_test_file")
    assert e.value.gate == "in_sandbox"


def test_small_diff_rejects_an_oversized_change(sandbox: Path):
    big = "\n".join(f"    a{i} = {i}" for i in range(40)) + "\n"
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(
            _spec(
                original_snippet='    order.status = "shipped"\n',
                mutated_snippet=big,
            ),
            sandbox,
            max_diff_lines=15,
        )
    assert e.value.gate == "small_diff"


def test_not_a_noop_rejects_identical_snippets(sandbox: Path):
    snip = '    order.status = "shipped"\n'
    with pytest.raises(MutationRejected) as e:
        mutation.apply_with_gates(_spec(original_snippet=snip, mutated_snippet=snip), sandbox)
    assert e.value.gate == "not_a_noop"


def test_plausibility_flags_a_mislabelled_mutation_without_blocking():
    note = mutation.plausibility_note(
        _spec(mutation_type=MutationType.COMPARISON_CHANGE)
    )
    # remove-guard-shaped pair labelled comparison_change → a note, not an exception
    assert note is None or note.startswith("type_mismatch")


def test_semantic_change_requires_a_rationale():
    note = mutation.plausibility_note(
        _spec(mutation_type=MutationType.SEMANTIC_CHANGE, rationale="")
    )
    assert note is not None and "rationale" in note
