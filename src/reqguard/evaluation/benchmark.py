"""Benchmark harness: assemble stored artifacts into evaluator inputs (task C5).

Owned by Track C (Evidence Layer). Doc 3 §§16.3, 17.

Like ``metrics.py``, this module makes **no model call** and imports nothing
from ``reqguard.agent``. Its job is the deterministic glue between what a run
produced (per-requirement verdicts, mutation records, a trajectory) and the
pure scoring functions in ``metrics.py``. It also serialises the evaluator's
output to ``metrics.json`` and a human-readable ``comparison.md`` under
``benchmarks/results/<timestamp>/`` (§17).

The run itself — the part that calls the model — belongs to the orchestrator
(Track S) and the baseline (Track B). This module only *scores* what they
recorded, which is exactly what makes re-scoring free and deterministic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from reqguard.evaluation.ground_truth import GroundTruth, load_all_ground_truths
from reqguard.evaluation.metrics import (
    ArmMetrics,
    benchmarks_tree_sha256,
    evaluate_arm,
)

__all__ = [
    "ScoredRequirement",
    "MutationRecord",
    "ArmArtifacts",
    "verdict_reports_gap",
    "verdict_is_inconclusive",
    "scored_requirements_from_verdicts",
    "evaluate_run",
    "write_results",
    "metrics_to_dict",
]

# Verdict.status values (models.Verdict) that mean "this requirement is a gap".
# REPAIRED means a gap was found AND fixed — still a detected gap for scoring.
_GAP_STATUSES = frozenset({"INSUFFICIENTLY_PROTECTED", "REPAIRED"})
_INCONCLUSIVE_STATUSES = frozenset({"INCONCLUSIVE"})


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ── the scored-requirement record the evaluator consumes ────────────────────

@dataclass(frozen=True)
class ScoredRequirement:
    case_id: str
    requirement_id: str
    is_gap: bool          # ground truth: protected == false
    reported_gap: bool    # the arm reported an insufficiency
    inconclusive: bool    # the arm produced no usable verdict
    clean_case: bool      # requirement belongs to a clean case


@dataclass(frozen=True)
class MutationRecord:
    mutation_id: str
    target_file: str
    diff: str = ""
    survived: bool | None = None  # None == inconclusive
    killed: bool | None = None
    repair_attempted: bool = False
    repair_verified: bool = False


@dataclass
class ArmArtifacts:
    """Everything one arm produced for a whole benchmark run."""

    arm: str
    scored_requirements: list[ScoredRequirement] = field(default_factory=list)
    mutations: list[MutationRecord] = field(default_factory=list)
    trajectory_path: str | None = None
    runtime_seconds: float | None = None
    is_reqguard: bool = False


# ── deriving the yes/no answer from a models.Verdict (or a dict) ────────────

def verdict_reports_gap(verdict: Any) -> bool:
    """True if an arm's verdict reports the requirement as a gap.

    Works on a ``reqguard.models.Verdict`` (``.status``) or a plain dict
    (``{"status": ...}``) loaded from ``findings.json``.
    """
    status = _status_str(verdict)
    return status in _GAP_STATUSES


def verdict_is_inconclusive(verdict: Any) -> bool:
    return _status_str(verdict) in _INCONCLUSIVE_STATUSES


def _status_str(verdict: Any) -> str:
    status = _get(verdict, "status")
    # tolerate an Enum-like with a .value, or a bare string
    return str(_get(status, "value", status) if status is not None else "")


def scored_requirements_from_verdicts(
    ground_truths: Mapping[str, GroundTruth],
    verdicts_by_case: Mapping[str, Iterable[Any]],
) -> list[ScoredRequirement]:
    """Join ground truth with an arm's verdicts into scored records.

    ``verdicts_by_case`` maps case_id -> iterable of verdicts. A verdict is
    matched to a ground-truth requirement by requirement id. A ground-truth
    requirement with NO matching verdict is scored as *inconclusive* (the arm
    said nothing about it) rather than silently as a correct negative — an
    absent answer must never read as a passed check.
    """
    scored: list[ScoredRequirement] = []

    for case_id, gt in sorted(ground_truths.items()):
        verdicts = list(verdicts_by_case.get(case_id, []))
        by_req: dict[str, Any] = {}
        for v in verdicts:
            req_id = _verdict_requirement_id(v)
            if req_id:
                by_req[req_id] = v

        for req in gt.requirements:
            v = by_req.get(req.id)
            if v is None:
                reported = False
                inconclusive = True
            else:
                inconclusive = verdict_is_inconclusive(v)
                reported = (not inconclusive) and verdict_reports_gap(v)
            scored.append(
                ScoredRequirement(
                    case_id=case_id,
                    requirement_id=req.id,
                    is_gap=not req.protected,
                    reported_gap=reported,
                    inconclusive=inconclusive,
                    clean_case=gt.clean,
                )
            )
    return scored


def _verdict_requirement_id(verdict: Any) -> str | None:
    # models.Verdict nests the id under .requirement.id; dicts may be flat.
    req = _get(verdict, "requirement")
    if req is not None:
        rid = _get(req, "id")
        if rid:
            return str(rid)
    rid = _get(verdict, "requirement_id")
    return str(rid) if rid else None


# ── top-level evaluation over one or both arms ──────────────────────────────

def evaluate_run(
    arms: Iterable[ArmArtifacts],
    benchmarks_dir: str | Path,
) -> dict[str, Any]:
    """Score every arm and package a JSON-serialisable metrics document.

    The benchmarks tree SHA-256 (§15.6 freeze rule) is recorded once at the top
    level. Each arm is scored by ``metrics.evaluate_arm``; the ReqGuard arm
    additionally gets mutation kill / repair rates.
    """
    tree_hash = benchmarks_tree_sha256(benchmarks_dir)
    arm_metrics: dict[str, ArmMetrics] = {}

    for art in arms:
        arm_metrics[art.arm] = evaluate_arm(
            art.arm,
            art.scored_requirements,
            mutations=art.mutations,
            trajectory_path=art.trajectory_path,
            runtime_seconds=art.runtime_seconds,
            compute_mutation_metrics=art.is_reqguard,
        )

    return {
        "benchmarks_sha256": tree_hash,
        "generated_at": _now_iso(),
        "arms": {name: metrics_to_dict(m) for name, m in arm_metrics.items()},
    }


def metrics_to_dict(m: ArmMetrics) -> dict[str, Any]:
    """Convert an ArmMetrics (nested frozen dataclasses) to plain JSON types."""
    return _to_jsonable(m)


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Mapping):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── serialisation to benchmarks/results/<timestamp>/ ────────────────────────

def write_results(
    metrics_doc: Mapping[str, Any],
    results_root: str | Path,
    *,
    timestamp: str | None = None,
) -> Path:
    """Write ``metrics.json`` and ``comparison.md`` under a timestamped dir.

    Returns the directory. ``timestamp`` is injectable so tests are
    deterministic; production uses the wall clock.
    """
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(results_root) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "comparison.md").write_text(
        render_comparison(metrics_doc), encoding="utf-8"
    )
    return out_dir


def _pct(value: Any) -> str:
    """Render a ratio as a percentage, or an explicit 'n/a' when undefined.

    Undefined (None) is NEVER shown as 0% — that would read as a measured zero.
    """
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def render_comparison(metrics_doc: Mapping[str, Any]) -> str:
    """Render the §16.3 comparison table plus the ReqGuard-only secondary
    numbers and the always-present inconclusive rate."""
    arms = metrics_doc.get("arms", {})
    lines: list[str] = ["# ReqGuard benchmark comparison", ""]
    lines.append(f"benchmarks SHA-256: `{metrics_doc.get('benchmarks_sha256', '')}`")
    lines.append("")

    # Primary, comparable table
    lines.append("## Gap detection (both arms)")
    lines.append("")
    lines.append("| metric | " + " | ".join(arms.keys()) + " |")
    lines.append("|---|" + "|".join(["---"] * len(arms)) + "|")
    for label, key in (
        ("recall", "recall"),
        ("precision", "precision"),
        ("clean-case FP rate", "clean_case_fp_rate"),
        ("inconclusive rate", "inconclusive_rate"),
    ):
        row = [label]
        for arm in arms.values():
            row.append(_pct(arm.get("gap_detection", {}).get(key)))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Secondary, ReqGuard only
    lines.append("## Mutation kill / repair (ReqGuard only)")
    lines.append("")
    any_mut = False
    for name, arm in arms.items():
        mut = arm.get("mutation")
        if not mut:
            continue
        any_mut = True
        lines.append(f"- **{name}** mutation kill rate: {_pct(mut.get('kill_rate'))} "
                     f"({mut.get('mutations_killed')}/{mut.get('mutations_attempted')})")
        lines.append(f"- **{name}** repair success rate: "
                     f"{_pct(mut.get('repair_success_rate'))} "
                     f"({mut.get('repairs_verified')}/{mut.get('repairs_attempted')})")
    if not any_mut:
        lines.append("_No arm produced mutations (baseline structurally cannot)._")
    lines.append("")

    # Cost + runtime, per arm
    lines.append("## Cost and runtime")
    lines.append("")
    for name, arm in arms.items():
        cost = arm.get("cost", {})
        lines.append(
            f"- **{name}**: {cost.get('api_calls', 0)} api calls, "
            f"{cost.get('input_tokens', 0)} in / {cost.get('output_tokens', 0)} out "
            f"tokens, runtime {arm.get('runtime_seconds')}s"
        )
    lines.append("")
    return "\n".join(lines)


# ── convenience: load ground truths for a benchmarks dir ────────────────────

def load_ground_truths(benchmarks_dir: str | Path) -> dict[str, GroundTruth]:
    return load_all_ground_truths(benchmarks_dir)
