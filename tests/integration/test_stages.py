"""Integration tests for the agent stages. Owned by Track B.

Two kinds of test live here:

- Wiring tests (default): a fake Gemini client is injected. They assert the
  call SHAPE — which prompt, which thinking level, structured vs. tool-using,
  how the document is framed in the user turn — and that the stage returns the
  frozen contract type. No API key, no spend, CI-safe.
- Live tests (`@live`, skipped without GEMINI_API_KEY/GOOGLE_API_KEY): one real
  call against case01, for manual tuning. Never required by a gate.

**Migration note (COLLAB.md session 6):** this file predates the Gemini
migration and was never committed (leftover WIP from the earlier 5-agent
phase) — its fake client mocked Anthropic's `messages.parse`/
`beta.messages.tool_runner` shapes, which no longer exist in `client.py`.
Updated to fake `models.generate_content` instead: both `structured()` and
`run_with_tools()` now go through that one call surface (Gemini's automatic
function calling folds the tool loop into the same call), so there is no more
`calls` vs `tool_calls` split — one `.calls` list, distinguished by whether
`config["tools"]` is present.

Gate: `pytest tests/integration/test_stages.py -k <stage> -q`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from reqguard.agent import client as client_mod
from reqguard.models import Behavior, Category, Requirement, Severity

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE01 = REPO_ROOT / "benchmarks" / "case01-cancelled-order-shipping"

live = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="live test — needs OPENROUTER_API_KEY",
)


# ── fake client (OpenAI/OpenRouter surface) ─────────────────────────────────
# Migration note (COLLAB.md): Anthropic -> Gemini -> OpenRouter. The client now
# uses the `openai` SDK against OpenRouter. `structured()` calls
# `beta.chat.completions.parse` (parsed on choices[0].message.parsed);
# `run_with_tools()` calls `chat.completions.create` (tool_calls loop) then a
# final `.parse`. The fake below mimics both surfaces and records every call's
# kwargs on `.calls`; `.tool_calls` is the subset that passed `tools=`.

class _FakeUsage:
    prompt_tokens = 1000
    completion_tokens = 200
    total_tokens = 1250

    def model_dump(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class _FakeMessage:
    def __init__(self, parsed: object = None, content: str = "", tool_calls=None) -> None:
        self.parsed = parsed
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, message: _FakeMessage, finish_reason: str = "stop") -> None:
        self.choices = [_FakeChoice(message, finish_reason)]
        self.usage = _FakeUsage()


class _FakeParse:
    """Fakes `beta.chat.completions.parse`. Returns the parsed_output the test
    configured. If the client wrapped a list[...] in an envelope, honor that by
    returning an object exposing `.items`."""

    def __init__(self, parsed_output: object, calls: list[dict]) -> None:
        self._parsed = parsed_output
        self._calls = calls

    def parse(self, **kwargs):  # noqa: ANN003
        self._calls.append(kwargs)
        rf = kwargs.get("response_format")
        parsed = self._parsed
        # list[...] envelope: client passes a generated model with an `items`
        # field and unwraps `.items`. Mimic by wrapping the list.
        if isinstance(parsed, list) and rf is not None and "items" in getattr(rf, "model_fields", {}):
            class _Env:
                items = parsed
            parsed = _Env()
        return _FakeResponse(_FakeMessage(parsed=parsed))


class _FakeCompletions:
    """Fakes both `beta.chat.completions.parse` and `chat.completions.create`.
    `create` (tool path) returns a plain no-tool-call message so the loop
    finishes in one round; the final structured answer comes from `parse`."""

    def __init__(self, parsed_output: object, calls: list[dict]) -> None:
        self._parse = _FakeParse(parsed_output, calls)
        self._calls = calls

    def create(self, **kwargs):  # noqa: ANN003
        self._calls.append(kwargs)
        # No tool_calls -> run_with_tools breaks out of the loop immediately,
        # then issues the final .parse for the structured result.
        return _FakeResponse(_FakeMessage(content="done", tool_calls=None))

    def parse(self, **kwargs):  # noqa: ANN003
        return self._parse.parse(**kwargs)


class _FakeBeta:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = type("_Chat", (), {"completions": completions})()


class _FakeClient:
    def __init__(self, parsed_output: object) -> None:
        self.calls: list[dict] = []
        completions = _FakeCompletions(parsed_output, self.calls)
        self.beta = _FakeBeta(completions)
        self.chat = type("_Chat", (), {"completions": completions})()

    @property
    def tool_calls(self) -> list[dict]:
        """The subset of calls that declared tools (the create() path)."""
        return [c for c in self.calls if c.get("tools")]


@pytest.fixture
def fake_client(monkeypatch):
    """Factory: call with the parsed_output the fake should yield; patches
    `reqguard.agent.client._client` directly so `_get_client()` returns the fake
    and never constructs a real OpenAI client (no key needed). Yields the fake
    so the test can inspect `.calls` / `.tool_calls`."""

    def _install(parsed_output: object) -> _FakeClient:
        fc = _FakeClient(parsed_output)
        monkeypatch.setattr(client_mod, "_client", fc)
        return fc

    return _install


# Track A's `tools/repository.py` (task A2) is the dependency B3/B5/B6 bind to.
# Until it lands, the Tool Runner stages cannot build their tool surface.
_HAS_A2 = True
try:  # pragma: no cover - import probe
    import reqguard.tools.repository  # noqa: F401
except Exception:  # noqa: BLE001
    _HAS_A2 = False

needs_a2 = pytest.mark.skipif(not _HAS_A2, reason="needs Track A task A2 (tools/repository.py)")


# ── Stage 1 — analyzer ──────────────────────────────────────────────────────

_REQ_01 = Requirement(
    id="REQ-01",
    statement=(
        "A cancelled order cannot be shipped. Attempting to ship a\n"
        "cancelled order must be rejected."
    ),
    behavior=Behavior(
        precondition='order.status == "cancelled"',
        prohibited_action="ship_order(order)",
        expected_result="operation rejected",
    ),
    category=Category.BUSINESS_RULE,
    severity=Severity.HIGH,
    source_location="requirements.md:L3",
)


def test_analyzer_returns_frozen_requirements_and_uses_the_right_call(fake_client):
    from reqguard.agent import requirement_analyzer

    fc = fake_client([_REQ_01])
    out = requirement_analyzer.run(CASE01 / "requirements.md")

    assert isinstance(out, list) and len(out) == 1
    assert isinstance(out[0], Requirement)
    assert out[0].id == "REQ-01"

    assert len(fc.calls) == 1
    k = fc.calls[0]
    assert k["model"] == client_mod.MODEL
    assert k["reasoning_effort"] == "medium"  # analyze policy
    # the analyze prompt, verbatim, nothing interpolated
    from reqguard.agent import prompts
    assert k["messages"][0] == {"role": "system", "content": prompts.load("analyze")}
    # the document rides in the user turn, with line numbers for source_location
    user = k["messages"][1]["content"]
    assert "requirements.md:Lnn" in user
    assert "L1: # Order Requirements" in user
    assert "cancelled order" in user


def test_analyzer_reads_the_named_file(fake_client, tmp_path):
    from reqguard.agent import requirement_analyzer

    doc = tmp_path / "specs.md"
    doc.write_text("REQ-9: The widget must not overheat.\n", encoding="utf-8")
    fc = fake_client([])
    requirement_analyzer.run(doc)

    user = fc.calls[0]["messages"][1]["content"]
    assert "specs.md:Lnn" in user
    assert "widget must not overheat" in user


@live
def test_analyzer_live_extracts_req_01_from_case01():
    from reqguard.agent import requirement_analyzer

    out = requirement_analyzer.run(CASE01 / "requirements.md")
    assert any(r.id == "REQ-01" or "cancelled" in r.statement.lower() for r in out)
    for r in out:
        assert r.behavior.prohibited_action  # concrete, non-empty


# ── shared fixtures for the later stages ────────────────────────────────────

from reqguard.models import (  # noqa: E402
    Investigation,
    MutationSpec,
    MutationType,
    RepairProposal,
    RunStatus,
    Site,
)
from reqguard.models import TestOutcome as _TestOutcome  # noqa: E402  (aliased: pytest tries to collect a bare `Test*` name)

_INV_01 = Investigation(
    requirement_id="REQ-01",
    sites=[
        Site(
            file="src/orders/service.py",
            symbol="ship_order",
            start_line=11,
            end_line=15,
            enforcement_snippet='if order.status == "cancelled":\n    raise OrderCancelledError(order.id)',
            confidence="high",
        )
    ],
    covering_tests=["tests/test_orders.py::test_open_order_ships"],
    domain_notes="Order.status is one of open|cancelled|shipped.",
    rationale="ship_order holds the only cancellation guard.",
)

_SPEC_01 = MutationSpec(
    id="MUT-01",
    requirement_id="REQ-01",
    mutation_type=MutationType.REMOVE_GUARD,
    target_file="src/orders/service.py",
    target_symbol="ship_order",
    original_snippet='if order.status == "cancelled":\n        raise OrderCancelledError(order.id)\n',
    mutated_snippet="",
    description="Remove the cancelled-order guard",
    expected_violation="A cancelled order can be shipped",
    violation_scenario="ship_order(Order(id='X', status='cancelled', quantity=1))",
    rationale="Deleting the guard is the smallest change that violates REQ-01.",
)

_MUTATED_SURVIVED = _TestOutcome(
    status=RunStatus.OK, exit_code=0, passed=2, failed=0, stdout_tail="2 passed"
)

_REPAIR_01 = RepairProposal(
    requirement_id="REQ-01",
    mutation_id="MUT-01",
    test_path="tests/test_orders_cancelled.py",
    test_source=(
        "import pytest\n"
        "from orders.service import Order, ship_order, OrderCancelledError\n\n"
        "def test_cancelled_order_cannot_be_shipped():\n"
        "    order = Order(id='C1', status='cancelled', quantity=1)\n"
        "    with pytest.raises(OrderCancelledError):\n"
        "        ship_order(order)\n"
    ),
    explanation="Builds the cancelled precondition and asserts the rejection.",
    rationale="pytest.raises(OrderCancelledError) is the assertion that does the work.",
)


# ── Stage 3 — planner (structured, no tools; runs without Track A) ──────────

def test_planner_uses_xhigh_structured_call_with_sites_and_tests(fake_client):
    from reqguard.agent import mutation_planner, prompts

    fc = fake_client(_SPEC_01)
    out = mutation_planner.run(CASE01, _REQ_01, _INV_01)

    assert isinstance(out, MutationSpec)
    assert out.requirement_id == "REQ-01"

    assert len(fc.calls) == 1 and not fc.tool_calls
    k = fc.calls[0]
    assert k["model"] == client_mod.MODEL
    assert k["reasoning_effort"] == "high"  # xhigh -> high (plan policy)
    assert k["response_format"] is MutationSpec
    assert k["messages"][0]["content"] == prompts.load("plan")

    user = k["messages"][1]["content"]
    assert "REQ-01" in user
    assert "src/orders/service.py" in user and "ship_order" in user   # the site
    assert "test_open_order_ships" in user                            # existing tests inlined


def test_planner_reports_empty_sites_without_inventing_one(fake_client):
    from reqguard.agent import mutation_planner

    empty = Investigation(requirement_id="REQ-01", rationale="no site")
    fc = fake_client(_SPEC_01)
    mutation_planner.run(CASE01, _REQ_01, empty)

    user = fc.calls[0]["messages"][1]["content"]
    assert "none" in user.lower()


def test_planner_rejects_semantic_change_without_a_rationale(fake_client):
    from reqguard.agent import mutation_planner
    from reqguard.errors import AgentError

    bad = _SPEC_01.model_copy(
        update={"mutation_type": MutationType.SEMANTIC_CHANGE, "rationale": "   "}
    )
    fake_client(bad)
    with pytest.raises(AgentError):
        mutation_planner.run(CASE01, _REQ_01, _INV_01)

    ok = bad.model_copy(update={"rationale": "no catalogue type expresses an 80-of-100 completion"})
    fake_client(ok)
    out = mutation_planner.run(CASE01, _REQ_01, _INV_01)
    assert out.mutation_type is MutationType.SEMANTIC_CHANGE


# ── Stage 2 — investigator (Tool Runner + Track A tools) ───────────────────

@needs_a2
def test_investigator_binds_the_seven_tools_in_sorted_order(fake_client):
    from reqguard.agent import repository_investigator, prompts
    from reqguard.tools import registry

    fc = fake_client(_INV_01)
    out = repository_investigator.run(CASE01, _REQ_01)

    assert isinstance(out, Investigation)
    assert len(fc.calls) >= 1 and len(fc.tool_calls) == 1
    # the tool-calling create() carries the tools + effort; find it
    k = fc.tool_calls[0]
    assert k["reasoning_effort"] == "high"  # investigate policy
    assert k["messages"][0]["content"] == prompts.load("investigate")

    names = registry.tool_names(k["tools"])
    assert names == list(registry.TOOL_NAMES)                # fixed, sorted order
    assert names == sorted(names)

    user = k["messages"][1]["content"]
    assert "REQ-01" in user and _REQ_01.behavior.prohibited_action in user


@needs_a2
def test_investigator_raises_on_non_stop_finish_reason(monkeypatch):
    """No partial answers accepted. A `finish_reason` other than stop/tool_calls
    -- length, content_filter, a tool call the model gave up on -- is a loud
    failure, not a silently truncated result (B3 acceptance / manual section
    10.3). Under OpenAI/OpenRouter this is the `finish_reason` check in
    `_assert_finished`; it replaces the Anthropic `pause_turn` test."""
    from reqguard.agent import repository_investigator
    from reqguard.agent import client as client_mod
    from reqguard.errors import ReqGuardInternalError

    truncated = _FakeResponse(_FakeMessage(content="", tool_calls=None), finish_reason="length")

    class _Completions:
        @staticmethod
        def create(**_kwargs):
            return truncated

        @staticmethod
        def parse(**_kwargs):
            return truncated

    class _Client:
        chat = type("_Chat", (), {"completions": _Completions()})()
        beta = type("_Beta", (), {"chat": type("_C", (), {"completions": _Completions()})()})()

    monkeypatch.setattr(client_mod, "_client", _Client())
    with pytest.raises(ReqGuardInternalError):
        repository_investigator.run(CASE01, _REQ_01)


@needs_a2
def test_investigator_appends_reqguard_md_to_the_user_turn_only(fake_client, tmp_path):
    from reqguard.agent import repository_investigator

    (tmp_path / "REQGUARD.md").write_text("Ledger entries are append-only.", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    fc = fake_client(_INV_01)
    repository_investigator.run(tmp_path, _REQ_01)

    # REQGUARD.md rides in the user turn of the tool-calling call, never the
    # cached system prompt.
    k = fc.tool_calls[0]
    user = k["messages"][1]["content"]
    assert "append-only" in user
    assert "append-only" not in k["messages"][0]["content"]  # never the system prompt


# ── Stage 5 — repairer (Tool Runner + Track A tools) ───────────────────────

@needs_a2
def test_repairer_returns_a_proposal_and_feeds_back_prior_failures(fake_client):
    from reqguard.agent import test_repairer, prompts

    fc = fake_client(_REPAIR_01)
    out = test_repairer.run(
        CASE01,
        _REQ_01,
        _SPEC_01,
        _MUTATED_SURVIVED,
        prior_failures=[(_REPAIR_01, "test did not kill mutant (exit 0)")],
    )

    assert isinstance(out, RepairProposal)
    k = fc.tool_calls[0]
    assert k["reasoning_effort"] == "high"  # repair policy
    assert k["messages"][0]["content"] == prompts.load("repair")

    user = k["messages"][1]["content"]
    assert "did not kill mutant" in user                     # the verifier's reason, fed back
    assert _SPEC_01.violation_scenario in user


# ── the baseline control arm ──────────────────────────────────────────────

@needs_a2
def test_baseline_uses_the_same_model_and_highest_effort_and_same_tools(fake_client):
    from reqguard.evaluation import baseline
    from reqguard.agent import prompts
    from reqguard.tools import registry

    finding = baseline.BaselineFinding(
        requirement_id="REQ-01",
        status="INSUFFICIENTLY_PROTECTED",
        reasoning="No test builds a cancelled order.",
    )
    fc = fake_client(finding)
    out = baseline.assess(CASE01, _REQ_01)

    assert isinstance(out, baseline.BaselineFinding)
    assert out.status in ("PROTECTED", "INSUFFICIENTLY_PROTECTED")

    k = fc.tool_calls[0]
    assert k["model"] == client_mod.MODEL                    # SAME model as ReqGuard
    assert k["reasoning_effort"] == "high"  # xhigh -> high, deliberately highest
    assert k["messages"][0]["content"] == prompts.load("baseline")
    assert registry.tool_names(k["tools"]) == list(registry.TOOL_NAMES)  # same tool surface


def test_baseline_prompt_stays_short():
    """It is what a developer would actually type — padding it with ReqGuard's
    methodology builds a straw baseline in reverse (section 16.2)."""
    from reqguard.agent import prompts

    assert len(prompts.load("baseline")) < 600
