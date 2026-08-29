"""Tests for the report writer + terminal formatter (docs/03 section 12, 13.2).

Acceptance (crew/tasks.yaml C6):
  - writes summary.html, findings.json, mutations.json, (metrics.json), trajectory.jsonl
  - summary.html is self-contained — inline CSS, zero external assets
  - repairs written to the report dir, NEVER to the target repo
  - terminal block prints Inconclusive even at zero
"""

from __future__ import annotations

import json
from pathlib import Path

from reqguard.models import (
    Behavior,
    Category,
    MutationResult,
    MutationSpec,
    MutationType,
    Requirement,
    RepairProposal,
    RunStatus,
    Severity,
    Verdict,
    Verification,
)
from reqguard.models import TestOutcome as _TestOutcome  # aliased: pytest tries to collect a bare Test* name
from reqguard.reporting.formatter import render_terminal, summarize
from reqguard.reporting.report import write_report


def _req(rid: str, sev: Severity = Severity.HIGH) -> Requirement:
    return Requirement(
        id=rid,
        statement=f"{rid} statement",
        behavior=Behavior(precondition="p", prohibited_action="do()", expected_result="rejected"),
        category=Category.BUSINESS_RULE,
        severity=sev,
        source_location="requirements.md:L1",
    )


def _outcome(exit_code: int, passed: int = 2) -> _TestOutcome:
    return _TestOutcome(status=RunStatus.OK, exit_code=exit_code, passed=passed)


def _gap_verdict() -> Verdict:
    spec = MutationSpec(
        id="M1", requirement_id="REQ-01", mutation_type=MutationType.REMOVE_GUARD,
        target_file="src/x.py", target_symbol="f", original_snippet="a", mutated_snippet="b",
        description="d", expected_violation="v", violation_scenario="s",
    )
    return Verdict(
        requirement=_req("REQ-01"),
        status="REPAIRED",
        mutation=spec,
        result=MutationResult(mutation_id="M1", baseline=_outcome(0), mutated=_outcome(0), diff="--- a/src/x.py\n+++ b/src/x.py\n", survived=True),
        repair=RepairProposal(
            requirement_id="REQ-01", mutation_id="M1", test_path="tests/test_x_repair.py",
            test_source="def test_x():\n    assert True\n", explanation="e", rationale="r",
        ),
        verification=Verification(passes_on_original=True, kills_mutant=True, verified=True),
    )


def test_summarize_counts_by_status_and_severity():
    verdicts = [
        _gap_verdict(),
        Verdict(requirement=_req("REQ-02"), status="PROTECTED"),
        Verdict(requirement=_req("REQ-03"), status="INCONCLUSIVE", inconclusive_reason="env"),
    ]
    s = summarize(verdicts)
    assert s.analyzed == 3 and s.protected == 1 and s.gaps == 1 and s.inconclusive == 1
    assert s.repaired == 1 and s.high_risk_gaps == 1


def test_terminal_block_prints_inconclusive_even_at_zero():
    out = render_terminal([Verdict(requirement=_req("REQ-01"), status="PROTECTED")])
    assert "Inconclusive:          0" in out
    assert "Report:" in out


def test_write_report_materializes_every_file(tmp_path: Path):
    verdicts = [_gap_verdict()]
    traj = tmp_path / "trajectory.jsonl"
    traj.write_text('{"step":1,"type":"api_call"}\n', encoding="utf-8")

    out = write_report(verdicts, tmp_path / "report", repo="demo", trajectory_path=traj, metrics={"k": 1})

    for name in ("summary.html", "findings.json", "mutations.json", "metrics.json", "trajectory.jsonl"):
        assert (out / name).is_file(), name

    findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert findings[0]["requirement"]["id"] == "REQ-01"

    # Per-requirement artifacts land UNDER the report dir (Doc 3 section 12).
    assert (out / "repairs" / "REQ-01.py").is_file()
    assert (out / "repairs" / "REQ-01.py").read_text(encoding="utf-8").startswith("def test_x")
    assert (out / "mutations" / "REQ-01.diff").is_file()
    assert "--- a/src/x.py" in (out / "mutations" / "REQ-01.diff").read_text(encoding="utf-8")

    html = (out / "summary.html").read_text(encoding="utf-8")
    assert "<style>" in html
    assert "http://" not in html and "https://" not in html   # zero external assets
    assert "cdn" not in html.lower()


def test_write_report_does_not_write_into_any_source_tree(tmp_path: Path):
    repo = tmp_path / "userrepo"
    (repo / "tests").mkdir(parents=True)
    write_report([_gap_verdict()], tmp_path / "report", repo=str(repo))
    assert not (repo / "tests" / "test_x_repair.py").exists()
