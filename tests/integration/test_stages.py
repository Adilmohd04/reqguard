"""Integration tests for the agent stages. Owned by Track B.

Two kinds of test live here:

- Wiring tests (default): a fake Anthropic client is injected. They assert the
  call SHAPE — which prompt, which effort, structured vs. tool-runner, how the
  document is framed in the user turn — and that the stage returns the frozen
  contract type. No API key, no spend, CI-safe.
- Live tests (`@live`, skipped without ANTHROPIC_API_KEY): one real call against
  case01, for manual tuning. Never required by a gate.

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
    not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"),
    reason="live test — needs ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN",
)


# ── fake client ─────────────────────────────────────────────────────────────

class _FakeUsage:
    input_tokens = 1000
    output_tokens = 200
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 1000


class _FakeParsed:
    def __init__(self, parsed_output: object) -> None:
        self.parsed_output = parsed_output
        self.stop_reason = "end_turn"
        self._request_id = "req_fake_0001"
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self, parsed_output: object, calls: list[dict]) -> None:
        self._parsed_output = parsed_output
        self._calls = calls

    def parse(self, **kwargs):  # noqa: ANN003
        self._calls.append(kwargs)
        return _FakeParsed(self._parsed_output)


class _FakeClient:
    def __init__(self, parsed_output: object) -> None:
        self.calls: list[dict] = []
        self.messages = _FakeMessages(parsed_output, self.calls)


@pytest.fixture
def fake_client(monkeypatch):
    """Return a factory: call it with the parsed_output the fake should yield,
    and it patches `reqguard.agent.client._client`. Yields the fake so the test
    can inspect `.calls`."""

    def _install(parsed_output: object) -> _FakeClient:
        fc = _FakeClient(parsed_output)
        monkeypatch.setattr(client_mod, "_client", fc)
        return fc

    return _install


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
    assert k["model"] == "claude-opus-5"
    assert k["output_config"]["effort"] == "medium"          # analyze policy
    assert k["thinking"] == {"type": "adaptive"}
    assert k["system"][0]["cache_control"] == {"type": "ephemeral"}
    # the analyze prompt, verbatim, nothing interpolated
    from reqguard.agent import prompts
    assert k["system"][0]["text"] == prompts.load("analyze")
    # the document rides in the user turn, with line numbers for source_location
    user = k["messages"][0]["content"]
    assert "requirements.md:Lnn" in user
    assert "L1: # Order Requirements" in user
    assert "cancelled order" in user


def test_analyzer_reads_the_named_file(fake_client, tmp_path):
    from reqguard.agent import requirement_analyzer

    doc = tmp_path / "specs.md"
    doc.write_text("REQ-9: The widget must not overheat.\n", encoding="utf-8")
    fc = fake_client([])
    requirement_analyzer.run(doc)

    user = fc.calls[0]["messages"][0]["content"]
    assert "specs.md:Lnn" in user
    assert "widget must not overheat" in user


@live
def test_analyzer_live_extracts_req_01_from_case01():
    from reqguard.agent import requirement_analyzer

    out = requirement_analyzer.run(CASE01 / "requirements.md")
    assert any(r.id == "REQ-01" or "cancelled" in r.statement.lower() for r in out)
    for r in out:
        assert r.behavior.prohibited_action  # concrete, non-empty
