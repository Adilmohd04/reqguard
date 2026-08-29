"""The offline replay path — the whole pipeline with no model call, no key.

This is what `reqguard doctor --replay` and the CI demo run on. If the recorded
fixtures in `benchmarks/replay/` drift out of sync with the frozen contracts or
the orchestrator, this fails.

Gate: pytest tests/integration/test_replay.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE01 = REPO_ROOT / "benchmarks" / "case01-cancelled-order-shipping"
REPLAY = REPO_ROOT / "benchmarks" / "replay"
REPLAY_CASE01 = REPLAY / "case01-cancelled-order-shipping"

pytestmark = pytest.mark.skipif(
    not REPLAY_CASE01.is_dir(), reason="no benchmarks/replay/case01 fixtures"
)


def test_replay_fixtures_validate_against_the_frozen_contracts():
    from pydantic import TypeAdapter
    from reqguard.agent import replay
    from reqguard.models import Investigation, MutationSpec, RepairProposal, Requirement
    from reqguard.evaluation.baseline import BaselineFinding

    reqs = replay.requirements(REPLAY, CASE01.name)
    assert reqs and all(isinstance(r, Requirement) for r in reqs)
    TypeAdapter(list[Requirement]).validate_python(reqs)

    rid = reqs[0].id
    assert isinstance(replay.investigation(REPLAY, CASE01.name, rid), Investigation)
    assert isinstance(replay.mutation_spec(REPLAY, CASE01.name, rid), MutationSpec)
    assert isinstance(replay.repair(REPLAY, CASE01.name, rid), RepairProposal)
    assert isinstance(replay.baseline_finding(REPLAY, CASE01.name, rid), BaselineFinding)


def test_orchestrator_replays_case01_to_a_repaired_verdict(tmp_path):
    from reqguard.agent import orchestrator

    verdicts = orchestrator.analyze(CASE01, replay_dir=REPLAY, report_dir=tmp_path)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.status == "REPAIRED", (v.status, v.inconclusive_reason)
    assert v.result.survived is True
    assert v.verification.verified is True
    # the mutation planner's snippet was a real substring of the case file
    assert v.mutation.original_snippet in (
        CASE01 / "src/orders/service.py"
    ).read_text(encoding="utf-8")
    # source repo untouched
    assert not (CASE01 / "tests" / "test_orders_cancelled.py").exists()


def test_replay_never_falls_back_to_a_live_call_when_a_fixture_is_missing(tmp_path):
    from reqguard.agent import orchestrator

    # point at an empty replay dir → must raise loudly, never silently call the
    # model (that would spend money the caller asked to avoid).
    (tmp_path / CASE01.name).mkdir()
    with pytest.raises(FileNotFoundError, match="replay file missing"):
        orchestrator.analyze(CASE01, replay_dir=tmp_path, report_dir=tmp_path / "r")


def test_record_then_replay_round_trips(tmp_path, monkeypatch):
    """A recorded run reproduces byte-for-byte on replay."""
    from reqguard.agent import orchestrator

    rec = tmp_path / "rec"
    a = orchestrator.analyze(CASE01, replay_dir=REPLAY, record_dir=rec, report_dir=tmp_path / "a")
    b = orchestrator.analyze(CASE01, replay_dir=rec, report_dir=tmp_path / "b")
    assert [v.status for v in a] == [v.status for v in b]
    assert a[0].mutation.original_snippet == b[0].mutation.original_snippet
