"""Wave 2 — the orchestrator loop, end to end on case01 with NO model calls.

The four agent stages are replaced with canned outputs derived from case01; the
deterministic half — sandbox, test runner, mutation engine, verifier — runs for
real. This is the "hardcoded end-to-end on case01" gate from Doc 3 section 19.

Gate: pytest tests/integration/test_orchestrator.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reqguard.models import (
    Behavior,
    Category,
    Investigation,
    MutationSpec,
    MutationType,
    RepairProposal,
    Requirement,
    Severity,
    Site,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE01 = REPO_ROOT / "benchmarks" / "case01-cancelled-order-shipping"

_SERVICE = CASE01 / "src" / "orders" / "service.py"

_REQ = Requirement(
    id="REQ-01",
    statement="A cancelled order cannot be shipped.",
    behavior=Behavior(
        precondition='order.status == "cancelled"',
        prohibited_action="ship_order(order)",
        expected_result="operation rejected",
    ),
    category=Category.BUSINESS_RULE,
    severity=Severity.HIGH,
    source_location="requirements.md:L3",
)

_INV = Investigation(
    requirement_id="REQ-01",
    sites=[
        Site(
            file="src/orders/service.py",
            symbol="ship_order",
            start_line=11,
            end_line=15,
            enforcement_snippet='if order.status == "cancelled":',
            confidence="high",
        )
    ],
    covering_tests=["tests/test_orders.py::test_open_order_ships"],
    domain_notes="Order.status ∈ {open, cancelled, shipped}.",
    rationale="ship_order holds the only cancellation guard.",
)


def _guard_snippet() -> str:
    """The exact two guard lines, copied verbatim from the case source."""
    text = _SERVICE.read_text(encoding="utf-8")
    start = text.index("    if order.status ==")
    end = text.index("\n", text.index("raise OrderCancelledError")) + 1
    return text[start:end]


def _spec() -> MutationSpec:
    return MutationSpec(
        id="MUT-01",
        requirement_id="REQ-01",
        mutation_type=MutationType.REMOVE_GUARD,
        target_file="src/orders/service.py",
        target_symbol="ship_order",
        original_snippet=_guard_snippet(),
        mutated_snippet="",
        description="Remove the cancelled-order guard",
        expected_violation="A cancelled order can be shipped",
        violation_scenario="ship_order(Order(id='C1', status='cancelled', quantity=1))",
        rationale="Deleting the guard is the smallest change that violates REQ-01.",
    )


_REPAIR = RepairProposal(
    requirement_id="REQ-01",
    mutation_id="MUT-01",
    test_path="tests/test_orders_cancelled.py",
    test_source=(
        "import pytest\n"
        "from orders.service import Order, ship_order, OrderCancelledError\n\n\n"
        "def test_cancelled_order_cannot_be_shipped():\n"
        "    order = Order(id='C1', status='cancelled', quantity=1)\n"
        "    with pytest.raises(OrderCancelledError):\n"
        "        ship_order(order)\n"
    ),
    explanation="Builds the cancelled precondition and asserts the rejection.",
    rationale="pytest.raises(OrderCancelledError) is the assertion that does the work.",
)


@pytest.fixture()
def canned_stages(monkeypatch):
    from reqguard.agent import orchestrator

    monkeypatch.setattr(orchestrator.requirement_analyzer, "run", lambda *a, **k: [_REQ])
    monkeypatch.setattr(orchestrator.repository_investigator, "run", lambda *a, **k: _INV)
    monkeypatch.setattr(orchestrator.mutation_planner, "run", lambda *a, **k: _spec())
    monkeypatch.setattr(orchestrator.test_repairer, "run", lambda *a, **k: _REPAIR)
    return orchestrator


def test_case01_runs_end_to_end_to_a_repaired_verdict(canned_stages, tmp_path):
    verdicts = canned_stages.analyze(CASE01, only="REQ-01", report_dir=tmp_path)

    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.status == "REPAIRED", (v.status, v.inconclusive_reason, v.verification)

    # the deterministic middle actually happened
    assert v.result.baseline.exit_code == 0 and v.result.baseline.passed == 2
    assert v.result.survived is True                      # suite passed with the guard gone
    assert v.verification.passes_on_original is True
    assert v.verification.kills_mutant is True

    # artifacts landed in the report dir, NOT the case repo
    assert (tmp_path / "mutations" / "REQ-01.diff").is_file()
    assert (tmp_path / "repairs" / "REQ-01.py").is_file()
    assert not (CASE01 / "tests" / "test_orders_cancelled.py").exists()


def test_case01_source_repo_is_byte_identical_afterwards(canned_stages, tmp_path):
    before = _SERVICE.read_bytes()
    canned_stages.analyze(CASE01, only="REQ-01", report_dir=tmp_path)
    assert _SERVICE.read_bytes() == before


def test_no_enforcement_site_is_a_named_inconclusive(canned_stages, monkeypatch, tmp_path):
    monkeypatch.setattr(
        canned_stages.repository_investigator,
        "run",
        lambda *a, **k: Investigation(requirement_id="REQ-01", rationale="none"),
    )
    v = canned_stages.analyze(CASE01, only="REQ-01", report_dir=tmp_path)[0]
    assert v.status == "INCONCLUSIVE"
    assert "no enforcement site" in v.inconclusive_reason
