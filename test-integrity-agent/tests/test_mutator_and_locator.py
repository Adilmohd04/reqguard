"""Mutation generation, validation, and requirement-to-code location."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from test_integrity_agent.locator import (
    Locator,
    index_repository,
    rank_candidates,
    tokenize,
)
from test_integrity_agent.models import MutationSource, Requirement, SourceSpan
from test_integrity_agent.mutator import (
    ast_mutations,
    generate_mutations,
    is_cosmetic,
    llm_mutations,
    make_diff,
    read_span,
)

SOURCE = '''\
"""A module."""


def shipping_fee(subtotal):
    """Free above the threshold."""
    if subtotal >= 100:
        return 0.0
    return 7.5
'''

SPAN = SourceSpan(path="pkg/ship.py", start_line=4, end_line=8, symbol="shipping_fee")
REQUIREMENT = Requirement(
    id="REQ-005",
    text="Shipping is free only when the subtotal is at least 100.",
    source="REQUIREMENTS.md:5",
)


# --- validation -------------------------------------------------------------


def test_identical_source_is_cosmetic() -> None:
    assert is_cosmetic(SOURCE, SOURCE) is True


def test_comment_and_docstring_edits_are_cosmetic() -> None:
    changed = SOURCE.replace("Free above the threshold.", "Totally different words.")
    assert is_cosmetic(SOURCE, changed) is True


def test_whitespace_change_is_cosmetic() -> None:
    changed = SOURCE.replace("return 0.0", "return 0.0  ")
    assert is_cosmetic(SOURCE, changed) is True


def test_operator_change_is_not_cosmetic() -> None:
    changed = SOURCE.replace(">= 100", "> 100")
    assert is_cosmetic(SOURCE, changed) is False


def test_unparseable_replacement_is_not_treated_as_cosmetic() -> None:
    assert is_cosmetic(SOURCE, "def broken(:") is False


# --- span reading and splicing ---------------------------------------------


def test_read_span_extracts_the_function() -> None:
    piece = read_span(SOURCE, SPAN)
    assert piece is not None
    assert piece.text.startswith("def shipping_fee")
    assert piece.text.rstrip().endswith("return 7.5")


def test_read_span_rejects_an_out_of_range_span() -> None:
    assert read_span(SOURCE, SourceSpan(path="p", start_line=1, end_line=999)) is None


def test_splice_reindents_a_dedented_replacement() -> None:
    indented = "class A:\n    def f(self):\n        return 1\n"
    span = SourceSpan(path="p.py", start_line=2, end_line=3)
    piece = read_span(indented, span)
    assert piece is not None
    spliced = piece.splice("def f(self):\n    return 2")
    assert spliced is not None
    assert "    def f(self):" in spliced
    ast.parse(spliced)


def test_splice_refuses_a_replacement_that_cannot_be_made_to_parse() -> None:
    piece = read_span(SOURCE, SPAN)
    assert piece is not None
    assert piece.splice("def broken(:\n") is None


# --- AST operators ----------------------------------------------------------


def test_ast_mutations_are_valid_and_distinct() -> None:
    mutants = ast_mutations(REQUIREMENT, SPAN, SOURCE, count=3)
    assert mutants, "expected the comparison and the literals to be mutable"
    sources = {m.mutated_source for m in mutants}
    assert len(sources) == len(mutants)
    for mutant in mutants:
        ast.parse(mutant.mutated_source)
        assert mutant.mutated_source != SOURCE
        assert mutant.origin is MutationSource.AST
        assert mutant.operator
        assert mutant.requirement_id == "REQ-005"


def test_ast_mutations_flip_the_comparison_boundary() -> None:
    mutants = ast_mutations(REQUIREMENT, SPAN, SOURCE, count=6)
    operators = {m.operator for m in mutants}
    assert "comparison-boundary" in operators
    flipped = next(m for m in mutants if m.operator == "comparison-boundary")
    assert "subtotal > 100" in flipped.mutated_source


def test_ast_mutations_respect_the_count() -> None:
    assert len(ast_mutations(REQUIREMENT, SPAN, SOURCE, count=1)) == 1


def test_ast_mutations_on_code_with_nothing_to_mutate() -> None:
    source = "def f():\n    return\n"
    span = SourceSpan(path="p.py", start_line=1, end_line=2)
    assert ast_mutations(REQUIREMENT, span, source, count=3) == []


def test_ast_mutation_neuters_a_guard() -> None:
    source = "def withdraw(balance, amount):\n    if amount > balance:\n        raise ValueError('no')\n    return balance - amount\n"
    span = SourceSpan(path="p.py", start_line=1, end_line=4)
    mutants = ast_mutations(REQUIREMENT, span, source, count=8)
    assert any(m.operator == "guard-removed" for m in mutants)


def test_make_diff_shows_the_change() -> None:
    mutant = ast_mutations(REQUIREMENT, SPAN, SOURCE, count=1)[0]
    diff = make_diff(mutant)
    assert diff.startswith("---")
    assert any(line.startswith("+") for line in diff.splitlines())


# --- LLM mutations against a fake client -----------------------------------


class FakeLLM:
    """Stands in for :class:`LLMClient` without touching the network."""

    def __init__(self, payload: dict[str, Any], degraded: bool = False) -> None:
        self.payload = payload
        self.degraded = degraded
        self.degraded_reason = "fake"
        self.calls = 0

    def structured(self, **_: Any) -> dict[str, Any]:
        self.calls += 1
        return self.payload


def test_llm_mutations_accept_a_valid_edit() -> None:
    replacement = (
        "def shipping_fee(subtotal):\n"
        '    """Free above the threshold."""\n'
        "    if subtotal > 100:\n"
        "        return 0.0\n"
        "    return 7.5"
    )
    llm = FakeLLM(
        {
            "mutations": [
                {
                    "replacement": replacement,
                    "explanation": "made the threshold exclusive",
                    "violation": "free shipping is denied at exactly 100",
                }
            ]
        }
    )
    mutants = llm_mutations(llm, REQUIREMENT, SPAN, SOURCE, count=3)  # type: ignore[arg-type]
    assert len(mutants) == 1
    assert mutants[0].origin is MutationSource.LLM
    assert "subtotal > 100" in mutants[0].mutated_source
    assert "exclusive" in mutants[0].explanation


def test_llm_mutations_reject_cosmetic_and_broken_candidates() -> None:
    llm = FakeLLM(
        {
            "mutations": [
                {"replacement": read_span(SOURCE, SPAN).text, "explanation": "", "violation": ""},
                {"replacement": "def broken(:", "explanation": "", "violation": ""},
                {"replacement": "   ", "explanation": "", "violation": ""},
            ]
        }
    )
    assert llm_mutations(llm, REQUIREMENT, SPAN, SOURCE, count=3) == []  # type: ignore[arg-type]


def test_generate_mutations_falls_back_to_ast_without_an_llm(tmp_path: Path) -> None:
    mutants, note = generate_mutations(
        REQUIREMENT, [SPAN], lambda _p: SOURCE, count=2, llm=None
    )
    assert mutants
    assert all(m.origin is MutationSource.AST for m in mutants)
    assert "AST operators used" in note


def test_generate_mutations_prefers_the_llm() -> None:
    replacement = (
        "def shipping_fee(subtotal):\n"
        '    """Free above the threshold."""\n'
        "    if subtotal > 100:\n"
        "        return 0.0\n"
        "    return 7.5"
    )
    llm = FakeLLM({"mutations": [{"replacement": replacement, "explanation": "x", "violation": "y"}]})
    mutants, note = generate_mutations(
        REQUIREMENT, [SPAN], lambda _p: SOURCE, count=1, llm=llm  # type: ignore[arg-type]
    )
    assert note == ""
    assert mutants[0].origin is MutationSource.LLM


# --- locator ----------------------------------------------------------------


def test_tokenize_splits_snake_and_camel_case() -> None:
    tokens = tokenize("shipping_fee computeOrderTotal")
    assert {"shipping", "fee", "compute", "order", "total"} <= tokens
    assert "the" not in tokens


def test_index_repository_finds_functions_and_methods(tiny_repo: Path) -> None:
    symbols = index_repository(tiny_repo, [tiny_repo / "src"], frozenset())
    names = {s.qualname for s in symbols}
    assert "withdraw" in names
    withdraw = next(s for s in symbols if s.qualname == "withdraw")
    assert withdraw.path == "src/pkg/account.py"
    assert withdraw.start_line == 1
    assert "insufficient funds" in withdraw.source


def test_index_repository_errors_when_there_is_no_python(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    from test_integrity_agent.errors import RepositoryError

    with pytest.raises(RepositoryError, match="No Python files"):
        index_repository(tmp_path, [tmp_path / "empty"], frozenset())


def test_ranking_puts_the_relevant_symbol_first(tiny_repo: Path) -> None:
    symbols = index_repository(tiny_repo, [tiny_repo / "src"], frozenset())
    requirement = Requirement(
        id="REQ-001",
        text="A withdrawal must never leave the balance below zero.",
        source="x",
    )
    ranked = rank_candidates(requirement, symbols)
    assert ranked[0][1].qualname == "withdraw"
    assert ranked[0][0] > 0


def test_locator_without_an_llm_uses_lexical_ranking(tiny_repo: Path) -> None:
    symbols = index_repository(tiny_repo, [tiny_repo / "src"], frozenset())
    requirement = Requirement(
        id="REQ-001", text="A withdrawal must never leave the balance below zero.", source="x"
    )
    spans = Locator(symbols).locate(requirement)
    assert spans
    assert spans[0].symbol == "withdraw"
    assert spans[0].path == "src/pkg/account.py"


def test_locator_uses_the_model_selection_when_available(tiny_repo: Path) -> None:
    symbols = index_repository(tiny_repo, [tiny_repo / "src"], frozenset())
    requirement = Requirement(id="REQ-001", text="A withdrawal must never overdraw.", source="x")
    llm = FakeLLM({"selections": [{"index": 0, "confidence": 0.9, "reason": "guards the balance"}]})
    spans = Locator(symbols, llm=llm).locate(requirement)  # type: ignore[arg-type]
    assert spans[0].confidence == pytest.approx(0.9)
    assert spans[0].reason == "guards the balance"
    assert llm.calls == 1


def test_locator_ignores_low_confidence_selections(tiny_repo: Path) -> None:
    symbols = index_repository(tiny_repo, [tiny_repo / "src"], frozenset())
    requirement = Requirement(id="REQ-001", text="A withdrawal must never overdraw.", source="x")
    llm = FakeLLM({"selections": [{"index": 0, "confidence": 0.1, "reason": "weak"}]})
    spans = Locator(symbols, llm=llm).locate(requirement)  # type: ignore[arg-type]
    assert spans  # falls back to lexical
    assert spans[0].reason != "weak"
