"""Unit tests for the evaluator / metrics (task C5).

Acceptance (crew/tasks.yaml C5):
  - contains NO LLM call of any kind
  - gap-detection recall/precision — comparable across both arms
  - mutation kill rate + repair success rate — ReqGuard arm only
  - clean-case false-positive rate
  - inconclusive rate is REPORTED, never buried
  - cost summed from trajectory usage records, never estimated
  - re-asserts from recorded diffs that no mutation touched a test file
  - records a SHA-256 of the benchmarks/ tree into metrics.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reqguard.evaluation import metrics as M
from reqguard.evaluation import benchmark as B
from reqguard.evaluation.benchmark import (
    ArmArtifacts,
    MutationRecord,
    ScoredRequirement,
    evaluate_run,
    scored_requirements_from_verdicts,
    verdict_is_inconclusive,
    verdict_reports_gap,
    write_results,
)
from reqguard.evaluation.ground_truth import GroundTruth, GroundTruthRequirement
from reqguard.models import Category, Requirement, Behavior, Severity, Verdict
from reqguard.tracing.trajectory import Trajectory


# ── NO LLM call of any kind ─────────────────────────────────────────────────

def test_no_anthropic_or_agent_imports() -> None:
    # Mirror the integration gate: match actual import STATEMENTS at line start
    # (optionally indented), not prose mentions in a docstring.
    #
    # The guarantee is provider-AGNOSTIC: the evaluator must import NO model SDK
    # of any kind, so re-scoring stored artifacts never costs money and two
    # people scoring one run get one number. Track B migrated Anthropic -> Gemini
    # (COLLAB.md session 7); this guard now covers both, so the property holds
    # regardless of which provider the agent layer uses. If Track B ever adds a
    # third provider, add its import root here too.
    import re

    # (statement pattern, human label) — any model SDK is forbidden here.
    model_sdk_stmts = [
        (re.compile(r"^\s*(import anthropic|from anthropic)", re.M), "anthropic"),
        (re.compile(r"^\s*(import|from)\s+google\.genai", re.M), "google.genai"),
        (re.compile(r"^\s*(import|from)\s+google\.generativeai", re.M),
         "google.generativeai"),
    ]
    agent_stmt = re.compile(r"^\s*(import|from)\s+reqguard\.agent", re.M)

    src_root = Path(__file__).resolve().parents[2] / "src" / "reqguard" / "evaluation"
    for name in ("metrics.py", "benchmark.py"):
        text = (src_root / name).read_text(encoding="utf-8")
        for pattern, label in model_sdk_stmts:
            assert not pattern.search(text), f"{name} imports {label}"
        assert not agent_stmt.search(text), f"{name} imports reqguard.agent"


# ── gap-detection scoring, comparable across both arms ──────────────────────

def _rec(is_gap: bool, reported: bool, inconclusive: bool = False,
         clean: bool = False) -> dict:
    return {
        "is_gap": is_gap,
        "reported_gap": reported,
        "inconclusive": inconclusive,
        "clean_case": clean,
    }


def test_gap_detection_perfect_recall_precision() -> None:
    recs = [
        _rec(is_gap=True, reported=True),    # TP
        _rec(is_gap=True, reported=True),    # TP
        _rec(is_gap=False, reported=False),  # TN
    ]
    g = M.gap_detection_metrics(recs)
    assert g.recall == 1.0
    assert g.precision == 1.0
    assert g.confusion.true_positive == 2


def test_gap_detection_recall_and_precision_math() -> None:
    recs = [
        _rec(is_gap=True, reported=True),    # TP
        _rec(is_gap=True, reported=False),   # FN (missed)
        _rec(is_gap=False, reported=True),   # FP (over-reported)
    ]
    g = M.gap_detection_metrics(recs)
    assert g.recall == pytest.approx(1 / 2)      # 1 TP / (1 TP + 1 FN)
    assert g.precision == pytest.approx(1 / 2)   # 1 TP / (1 TP + 1 FP)


def test_gap_detection_same_function_scores_both_arms() -> None:
    # The SAME function scores baseline and reqguard — comparability by
    # construction (§16.3).
    baseline = [_rec(is_gap=True, reported=False), _rec(is_gap=True, reported=False)]
    reqguard = [_rec(is_gap=True, reported=True), _rec(is_gap=True, reported=True)]
    gb = M.gap_detection_metrics(baseline)
    gr = M.gap_detection_metrics(reqguard)
    assert gb.recall == 0.0
    assert gr.recall == 1.0


def test_recall_precision_none_when_undefined() -> None:
    # No gaps and none reported -> recall/precision undefined (not 0.0).
    g = M.gap_detection_metrics([_rec(is_gap=False, reported=False)])
    assert g.recall is None
    assert g.precision is None


# ── clean-case false-positive rate ──────────────────────────────────────────

def test_clean_case_fp_rate() -> None:
    recs = [
        _rec(is_gap=False, reported=True, clean=True),   # clean FP
        _rec(is_gap=False, reported=False, clean=True),  # clean TN
        _rec(is_gap=False, reported=False, clean=True),  # clean TN
    ]
    g = M.gap_detection_metrics(recs)
    assert g.clean_case_requirements == 3
    assert g.clean_case_false_positives == 1
    assert g.clean_case_fp_rate == pytest.approx(1 / 3)


def test_clean_case_fp_rate_none_with_no_clean_cases() -> None:
    g = M.gap_detection_metrics([_rec(is_gap=True, reported=True)])
    assert g.clean_case_fp_rate is None


# ── inconclusive rate: reported, never buried ───────────────────────────────

def test_inconclusive_rate_reported() -> None:
    recs = [
        _rec(is_gap=True, reported=True),                       # scored
        _rec(is_gap=True, reported=False, inconclusive=True),   # inconclusive
    ]
    g = M.gap_detection_metrics(recs)
    assert g.confusion.inconclusive == 1
    assert g.inconclusive_rate == pytest.approx(1 / 2)


def test_inconclusive_excluded_from_precision_recall() -> None:
    # an inconclusive gap must NOT count as a false negative
    recs = [_rec(is_gap=True, reported=False, inconclusive=True)]
    g = M.gap_detection_metrics(recs)
    assert g.confusion.false_negative == 0
    assert g.recall is None  # nothing scorable


def test_inconclusive_rate_present_even_at_zero() -> None:
    doc = evaluate_run(
        [ArmArtifacts(arm="reqguard", scored_requirements=[
            ScoredRequirement("c", "r", is_gap=True, reported_gap=True,
                              inconclusive=False, clean_case=False)
        ], is_reqguard=True)],
        _BENCH,
    )
    gd = doc["arms"]["reqguard"]["gap_detection"]
    assert "inconclusive_rate" in gd  # key present even when zero


# ── mutation kill rate + repair success rate: ReqGuard arm only ─────────────

def test_mutation_metrics_kill_and_repair() -> None:
    muts = [
        MutationRecord("m1", "src/a.py", survived=False),  # killed
        MutationRecord("m2", "src/a.py", survived=True),   # survived
        MutationRecord("m3", "src/a.py", survived=None),   # inconclusive -> excluded
        MutationRecord("m4", "src/a.py", survived=True,
                       repair_attempted=True, repair_verified=True),
    ]
    mm = M.mutation_metrics(muts)
    assert mm.mutations_attempted == 3          # m3 excluded
    assert mm.mutations_killed == 1
    assert mm.kill_rate == pytest.approx(1 / 3)
    assert mm.repairs_attempted == 1
    assert mm.repairs_verified == 1
    assert mm.repair_success_rate == 1.0


def test_inconclusive_mutation_never_counts_as_kill() -> None:
    mm = M.mutation_metrics([MutationRecord("m", "src/a.py", survived=None)])
    assert mm.mutations_attempted == 0
    assert mm.kill_rate is None


def test_baseline_arm_has_no_mutation_metrics() -> None:
    baseline = ArmArtifacts(arm="baseline", is_reqguard=False,
                            scored_requirements=[])
    reqguard = ArmArtifacts(arm="reqguard", is_reqguard=True,
                            scored_requirements=[],
                            mutations=[MutationRecord("m", "src/a.py",
                                                      survived=False)])
    doc = evaluate_run([baseline, reqguard], _BENCH)
    assert doc["arms"]["baseline"]["mutation"] is None
    assert doc["arms"]["reqguard"]["mutation"] is not None
    assert doc["arms"]["reqguard"]["mutation"]["kill_rate"] == 1.0


# ── cost summed from trajectory usage, never estimated ──────────────────────

def test_cost_summed_from_trajectory(tmp_path: Path) -> None:
    traj = tmp_path / "trajectory.jsonl"
    t = Trajectory(traj)
    t.api_call("claude-opus-5", "r1",
               {"input_tokens": 100, "output_tokens": 10,
                "cache_read_input_tokens": 90}, requirement_id="REQ-01")
    t.decision("not an api call", requirement_id="REQ-01")  # must be ignored
    t.api_call("claude-opus-5", "r2",
               {"input_tokens": 50, "output_tokens": 5,
                "cache_read_input_tokens": 40}, requirement_id="REQ-02")

    cost = M.sum_cost(traj)
    assert cost["api_calls"] == 2
    assert cost["input_tokens"] == 150
    assert cost["output_tokens"] == 15
    assert cost["cache_read_input_tokens"] == 130
    # no dollar figure is invented
    assert "usd" not in cost and "cost" not in cost


def test_cost_missing_trajectory_is_zero(tmp_path: Path) -> None:
    cost = M.sum_cost(tmp_path / "nope.jsonl")
    assert cost["api_calls"] == 0
    assert cost["input_tokens"] == 0


# ── re-assert no mutation touched a test file (§6.5) ────────────────────────

def test_is_test_file_detects_test_paths() -> None:
    assert M.is_test_file("tests/test_orders.py")
    assert M.is_test_file("test_foo.py")
    assert M.is_test_file("foo_test.py")
    assert M.is_test_file("conftest.py")
    assert M.is_test_file("pkg/tests/helpers.py")
    assert not M.is_test_file("src/orders/service.py")
    assert not M.is_test_file("src/latest.py")  # 'latest' is not 'test'


def test_assert_no_mutation_touched_tests_flags_target() -> None:
    muts = [MutationRecord("m", "tests/test_orders.py", diff="")]
    offenders = M.assert_no_mutation_touched_tests(muts)
    assert offenders == ["tests/test_orders.py"]


def test_assert_no_mutation_touched_tests_flags_diff_path() -> None:
    diff = (
        "--- a/tests/test_orders.py\n"
        "+++ b/tests/test_orders.py\n"
        "@@ -1 +1 @@\n-x\n+y\n"
    )
    muts = [MutationRecord("m", "src/ok.py", diff=diff)]
    offenders = M.assert_no_mutation_touched_tests(muts)
    assert "tests/test_orders.py" in offenders


def test_evaluate_arm_raises_on_test_file_mutation() -> None:
    with pytest.raises(ValueError, match="void"):
        M.evaluate_arm(
            "reqguard",
            [],
            mutations=[MutationRecord("m", "tests/test_x.py")],
            compute_mutation_metrics=True,
        )


def test_clean_run_does_not_raise() -> None:
    m = M.evaluate_arm(
        "reqguard",
        [],
        mutations=[MutationRecord("m", "src/ok.py", survived=False)],
        compute_mutation_metrics=True,
    )
    assert m.test_file_mutations == []


# ── SHA-256 of the benchmarks/ tree recorded into metrics.json ──────────────

def test_benchmarks_sha256_deterministic_and_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    (root / "case01").mkdir(parents=True)
    (root / "case01" / "a.py").write_text("print(1)\n", encoding="utf-8")
    h1 = M.benchmarks_tree_sha256(root)
    h2 = M.benchmarks_tree_sha256(root)
    assert h1 == h2 and len(h1) == 64  # deterministic, sha256 hex

    (root / "case01" / "a.py").write_text("print(2)\n", encoding="utf-8")
    assert M.benchmarks_tree_sha256(root) != h1  # content change is visible


def test_benchmarks_sha256_ignores_results_dir(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    (root / "case01").mkdir(parents=True)
    (root / "case01" / "a.py").write_text("x\n", encoding="utf-8")
    h1 = M.benchmarks_tree_sha256(root)
    (root / "results" / "run1").mkdir(parents=True)
    (root / "results" / "run1" / "metrics.json").write_text("{}", encoding="utf-8")
    assert M.benchmarks_tree_sha256(root) == h1  # results excluded


def test_benchmarks_sha256_ignores_pytest_python_cache_artifacts(
    tmp_path: Path,
) -> None:
    # Review defect: normal validation/test runs create __pycache__/*.pyc,
    # .pytest_cache, and other caches. These MUST NOT change the freeze hash.
    root = tmp_path / "benchmarks"
    case = root / "case01"
    case.mkdir(parents=True)
    case.joinpath("service.py").write_text("def f(): return 1\n", encoding="utf-8")
    case.joinpath("ground_truth.json").write_text('{"x": 1}', encoding="utf-8")
    h1 = M.benchmarks_tree_sha256(root)

    # simulate what pytest / python leave behind
    pycache = case / "__pycache__"
    pycache.mkdir()
    pycache.joinpath("service.cpython-312.pyc").write_bytes(b"\x00\x01volatile")
    (case / ".pytest_cache").mkdir()
    (case / ".pytest_cache" / "CACHEDIR.TAG").write_text("x", encoding="utf-8")
    (root / ".mypy_cache").mkdir()
    (root / ".mypy_cache" / "cache.data").write_text("y", encoding="utf-8")
    (root / ".ruff_cache").mkdir()
    (root / ".ruff_cache" / "z").write_text("z", encoding="utf-8")
    # a stray compiled file not inside __pycache__
    case.joinpath("stray.pyc").write_bytes(b"\x00compiled")

    assert M.benchmarks_tree_sha256(root) == h1  # unchanged by any of the above


def test_benchmarks_sha256_sensitive_to_source_change(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    case = root / "case01"
    case.mkdir(parents=True)
    case.joinpath("service.py").write_text("def f(): return 1\n", encoding="utf-8")
    h1 = M.benchmarks_tree_sha256(root)
    case.joinpath("service.py").write_text("def f(): return 2\n", encoding="utf-8")
    assert M.benchmarks_tree_sha256(root) != h1  # corpus source edit is visible


def test_benchmarks_sha256_sensitive_to_ground_truth_change(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    case = root / "case01"
    case.mkdir(parents=True)
    gt = case / "ground_truth.json"
    gt.write_text('{"protected": true}', encoding="utf-8")
    h1 = M.benchmarks_tree_sha256(root)
    gt.write_text('{"protected": false}', encoding="utf-8")
    assert M.benchmarks_tree_sha256(root) != h1  # ground-truth edit is visible


def test_benchmarks_sha256_sensitive_to_new_source_file(tmp_path: Path) -> None:
    root = tmp_path / "benchmarks"
    case = root / "case01"
    case.mkdir(parents=True)
    case.joinpath("service.py").write_text("x\n", encoding="utf-8")
    h1 = M.benchmarks_tree_sha256(root)
    # adding a real .py source file (not compiled) must change the hash
    case.joinpath("extra.py").write_text("y\n", encoding="utf-8")
    assert M.benchmarks_tree_sha256(root) != h1


def test_benchmarks_sha256_stable_before_and_after_running(tmp_path: Path) -> None:
    # End-to-end of the review's exact concern: the SAME committed corpus must
    # get the SAME hash before and after a run leaves cache artifacts behind.
    root = tmp_path / "benchmarks"
    case = root / "case13-partial-fulfillment"
    case.mkdir(parents=True)
    case.joinpath("service.py").write_text("def s(): ...\n", encoding="utf-8")
    case.joinpath("ground_truth.json").write_text('{"id": "REQ-13"}',
                                                   encoding="utf-8")
    before = M.benchmarks_tree_sha256(root)

    # "run" side effects
    (case / "__pycache__").mkdir()
    (case / "__pycache__" / "service.cpython-312.pyc").write_bytes(b"bytecode")
    (root / "results" / "20260829T130000Z").mkdir(parents=True)
    (root / "results" / "20260829T130000Z" / "metrics.json").write_text(
        '{"benchmarks_sha256": "..."}', encoding="utf-8")

    after = M.benchmarks_tree_sha256(root)
    assert before == after


def test_metrics_json_records_tree_hash(tmp_path: Path) -> None:
    doc = evaluate_run(
        [ArmArtifacts(arm="reqguard", is_reqguard=True)],
        _make_bench(tmp_path),
    )
    assert len(doc["benchmarks_sha256"]) == 64


# ── verdict -> scored requirement derivation ────────────────────────────────

def _requirement(req_id: str) -> Requirement:
    return Requirement(
        id=req_id,
        statement="x",
        behavior=Behavior(precondition="a", prohibited_action="b",
                          expected_result="c"),
        category=Category.BUSINESS_RULE,
        severity=Severity.HIGH,
        source_location="requirements.md:L1",
    )


def _verdict(req_id: str, status: str) -> Verdict:
    return Verdict(requirement=_requirement(req_id), status=status)


def test_verdict_reports_gap_and_inconclusive() -> None:
    assert verdict_reports_gap(_verdict("R", "INSUFFICIENTLY_PROTECTED"))
    assert verdict_reports_gap(_verdict("R", "REPAIRED"))
    assert not verdict_reports_gap(_verdict("R", "PROTECTED"))
    assert verdict_is_inconclusive(_verdict("R", "INCONCLUSIVE"))
    # also works on plain dicts (findings.json)
    assert verdict_reports_gap({"status": "INSUFFICIENTLY_PROTECTED",
                                "requirement_id": "R"})


def test_scored_requirements_join() -> None:
    gt = {
        "case01": GroundTruth(
            case_id="case01", category=Category.BUSINESS_RULE,
            difficulty="medium", clean=False,
            requirements=[
                GroundTruthRequirement(id="REQ-01", statement="s",
                                       protected=False, rationale="gap"),
                GroundTruthRequirement(id="REQ-02", statement="s",
                                       protected=True),
            ],
        )
    }
    verdicts = {
        "case01": [
            _verdict("REQ-01", "INSUFFICIENTLY_PROTECTED"),  # correct gap
            _verdict("REQ-02", "PROTECTED"),                 # correct negative
        ]
    }
    scored = scored_requirements_from_verdicts(gt, verdicts)
    by_id = {s.requirement_id: s for s in scored}
    assert by_id["REQ-01"].is_gap and by_id["REQ-01"].reported_gap
    assert not by_id["REQ-02"].is_gap and not by_id["REQ-02"].reported_gap


def test_missing_verdict_scored_as_inconclusive() -> None:
    gt = {
        "case01": GroundTruth(
            case_id="case01", category=Category.BUSINESS_RULE,
            difficulty="medium", clean=False,
            requirements=[GroundTruthRequirement(id="REQ-01", statement="s",
                                                 protected=False,
                                                 rationale="gap")],
        )
    }
    scored = scored_requirements_from_verdicts(gt, {"case01": []})
    assert scored[0].inconclusive is True
    assert scored[0].reported_gap is False


# ── results serialisation ───────────────────────────────────────────────────

def test_write_results_creates_metrics_and_comparison(tmp_path: Path) -> None:
    doc = evaluate_run(
        [
            ArmArtifacts(arm="baseline", is_reqguard=False, scored_requirements=[
                ScoredRequirement("c", "r", is_gap=True, reported_gap=False,
                                  inconclusive=False, clean_case=False)]),
            ArmArtifacts(arm="reqguard", is_reqguard=True, scored_requirements=[
                ScoredRequirement("c", "r", is_gap=True, reported_gap=True,
                                  inconclusive=False, clean_case=False)],
                mutations=[MutationRecord("m", "src/a.py", survived=False)]),
        ],
        _make_bench(tmp_path),
    )
    out = write_results(doc, tmp_path / "results", timestamp="20260829T120000Z")
    assert (out / "metrics.json").is_file()
    assert (out / "comparison.md").is_file()

    loaded = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert loaded["arms"]["reqguard"]["mutation"]["kill_rate"] == 1.0

    md = (out / "comparison.md").read_text(encoding="utf-8")
    assert "Gap detection" in md
    assert "inconclusive rate" in md          # never buried
    assert "ReqGuard only" in md
    assert loaded["benchmarks_sha256"] in md


def test_comparison_renders_none_as_na(tmp_path: Path) -> None:
    doc = evaluate_run(
        [ArmArtifacts(arm="baseline", is_reqguard=False, scored_requirements=[
            ScoredRequirement("c", "r", is_gap=False, reported_gap=False,
                              inconclusive=False, clean_case=False)])],
        _make_bench(tmp_path),
    )
    md = B.render_comparison(doc)
    assert "n/a" in md   # undefined recall/precision rendered as n/a, not 0%


# ── shared fixtures ─────────────────────────────────────────────────────────

def _make_bench(tmp_path: Path) -> Path:
    root = tmp_path / "benchmarks"
    (root / "case01").mkdir(parents=True, exist_ok=True)
    (root / "case01" / "x.py").write_text("x\n", encoding="utf-8")
    return root


# a real benchmarks dir for the tests that only need a hashable tree
_BENCH = Path(__file__).resolve().parents[2] / "benchmarks"
