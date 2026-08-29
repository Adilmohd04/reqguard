"""CLI helpers that drive a run and hand artifacts to the deterministic scorer.

Owned by the Supervisor (Wave 2). The scoring math lives in
``reqguard.evaluation.metrics`` / ``benchmark`` and makes no model call; this
module is the part that *does* spend budget (orchestrator + baseline arm) and
then persists what they produced so re-scoring is free.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reqguard.config import load_config
from reqguard.evaluation import benchmark as bench_mod
from reqguard.evaluation.benchmark import ArmArtifacts, MutationRecord
from reqguard.evaluation.ground_truth import load_all_ground_truths
from reqguard.models import Verdict

__all__ = [
    "run_benchmark_arms",
    "newest_results_dir",
    "score_results",
    "run_doctor",
    "render_trajectory",
]

_RESULTS_ROOT = "results"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _case_dirs(benchmarks_dir: Path, only: list[str] | None) -> list[Path]:
    dirs = sorted(d for d in benchmarks_dir.iterdir() if d.is_dir() and d.name.startswith("case"))
    return [d for d in dirs if not only or d.name in only]


def _mutation_records(verdicts: list[Verdict]) -> list[MutationRecord]:
    out: list[MutationRecord] = []
    for v in verdicts:
        if v.mutation is None:
            continue
        survived = v.result.survived if v.result else None
        out.append(
            MutationRecord(
                mutation_id=v.mutation.id,
                target_file=v.mutation.target_file,
                diff=v.result.diff if v.result else "",
                survived=survived,
                killed=(survived is False),
                repair_attempted=v.repair is not None,
                repair_verified=bool(v.verification and v.verification.verified),
            )
        )
    return out


def run_benchmark_arms(
    benchmarks_dir: str | Path,
    arm: str = "both",
    *,
    only_cases: list[str] | None = None,
    replay_dir: str | Path | None = None,
) -> Path:
    """Run the requested arm(s) over the corpus, persist raw verdicts +
    trajectories under ``benchmarks/results/<ts>/``, and return that dir.

    ``replay_dir`` (or ``$REQGUARD_REPLAY``) replays recorded stage outputs —
    no key, no spend — so the pipeline and the evaluator can be exercised end
    to end offline.
    """
    from reqguard.agent import orchestrator, replay as replay_mod, requirement_analyzer
    from reqguard.evaluation import baseline as baseline_arm
    from reqguard.sandbox import sandbox
    from reqguard.tracing.trajectory import Trajectory

    bench = Path(benchmarks_dir)
    out = bench / _RESULTS_ROOT / _stamp()
    out.mkdir(parents=True, exist_ok=True)
    arms = ["reqguard", "baseline"] if arm == "both" else [arm]
    rdir = replay_mod.resolve_dir(replay_dir)

    rq_by_case: dict[str, list[dict]] = {}
    bl_by_case: dict[str, list[dict]] = {}
    runtimes = {"reqguard": 0.0, "baseline": 0.0}

    for case_dir in _case_dirs(bench, only_cases):
        cfg = load_config(case_dir)
        if "reqguard" in arms:
            traj = Trajectory(out / f"{case_dir.name}.reqguard.jsonl")
            t0 = time.perf_counter()
            verdicts = orchestrator.analyze(
                case_dir, cfg, traj,
                report_dir=out / "reports" / case_dir.name,
                replay_dir=rdir,
            )
            runtimes["reqguard"] += time.perf_counter() - t0
            rq_by_case[case_dir.name] = [v.model_dump(mode="json") for v in verdicts]
        if "baseline" in arms:
            traj = Trajectory(out / f"{case_dir.name}.baseline.jsonl")
            t0 = time.perf_counter()
            if rdir is not None:
                reqs = replay_mod.requirements(rdir, case_dir.name)
            else:
                reqs = requirement_analyzer.run(case_dir / cfg.requirements_path)
            with sandbox(case_dir, cfg) as sb:
                findings = baseline_arm.run(sb, reqs, replay_dir=rdir, case=case_dir.name)
            runtimes["baseline"] += time.perf_counter() - t0
            bl_by_case[case_dir.name] = [f.model_dump(mode="json") for f in findings]

    (out / "reqguard.verdicts.json").write_text(json.dumps(rq_by_case, indent=2), encoding="utf-8")
    (out / "baseline.findings.json").write_text(json.dumps(bl_by_case, indent=2), encoding="utf-8")
    (out / "meta.json").write_text(
        json.dumps({"runtimes": runtimes, "arms": arms}, indent=2), encoding="utf-8"
    )
    return out


def newest_results_dir(benchmarks_dir: str | Path) -> Path | None:
    root = Path(benchmarks_dir) / _RESULTS_ROOT
    if not root.is_dir():
        return None
    subs = sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)
    return subs[0] if subs else None


def _baseline_verdict_dicts(findings: list[dict]) -> list[dict]:
    """Map a BaselineFinding dict to the {requirement_id, status} shape the
    scorer's ``verdict_reports_gap`` understands."""
    return [
        {"requirement_id": f["requirement_id"], "status": f["status"]}
        for f in findings
    ]


def score_results(results_dir: str | Path, benchmarks_dir: str | Path) -> dict[str, Any]:
    """Deterministically score persisted verdicts. No model calls."""
    rdir = Path(results_dir)
    bench = Path(benchmarks_dir)
    ground_truths = load_all_ground_truths(bench)
    meta = (
        json.loads((rdir / "meta.json").read_text(encoding="utf-8"))
        if (rdir / "meta.json").is_file()
        else {}
    )
    runtimes = meta.get("runtimes", {})

    arms: list[ArmArtifacts] = []

    rq_path = rdir / "reqguard.verdicts.json"
    if rq_path.is_file():
        rq_by_case = {
            cid: [Verdict.model_validate(v) for v in vs]
            for cid, vs in json.loads(rq_path.read_text(encoding="utf-8")).items()
        }
        scored = bench_mod.scored_requirements_from_verdicts(ground_truths, rq_by_case)
        muts: list[MutationRecord] = []
        for verds in rq_by_case.values():
            muts += _mutation_records(verds)
        arms.append(
            ArmArtifacts(
                arm="reqguard",
                scored_requirements=scored,
                mutations=muts,
                trajectory_path=None,
                runtime_seconds=runtimes.get("reqguard"),
                is_reqguard=True,
            )
        )

    bl_path = rdir / "baseline.findings.json"
    if bl_path.is_file():
        bl_by_case = {
            cid: _baseline_verdict_dicts(fs)
            for cid, fs in json.loads(bl_path.read_text(encoding="utf-8")).items()
        }
        scored = bench_mod.scored_requirements_from_verdicts(ground_truths, bl_by_case)
        arms.append(
            ArmArtifacts(
                arm="baseline",
                scored_requirements=scored,
                mutations=[],
                runtime_seconds=runtimes.get("baseline"),
                is_reqguard=False,
            )
        )

    doc = bench_mod.evaluate_run(arms, bench)
    bench_mod.write_results(doc, Path(benchmarks_dir) / _RESULTS_ROOT, timestamp=rdir.name + "-scored")
    return doc


# ── reqguard doctor — the six kill criteria (section 20) ────────────────────

def run_doctor(case: str | Path, replay_dir: str | Path | None = None) -> bool:
    """Run the full pipeline against ONE case and print the six-criterion
    checklist. Returns True only when all six pass.

    ``replay_dir`` (or ``$REQGUARD_REPLAY``) runs it offline from recorded stage
    outputs — the go/no-go check without a key or spend."""
    from reqguard.agent import orchestrator, replay as replay_mod
    from reqguard.tracing.trajectory import Trajectory

    import tempfile

    case_dir = Path(case).resolve()
    cfg = load_config(case_dir)
    with tempfile.TemporaryDirectory(prefix="reqguard-doctor-") as tmp:
        report_dir = Path(tmp)
        traj = Trajectory(report_dir / "trajectory.jsonl")
        verdicts = orchestrator.analyze(
            case_dir, cfg, traj, report_dir=report_dir,
            replay_dir=replay_mod.resolve_dir(replay_dir),
        )
        return _doctor_checklist(verdicts)


def _doctor_checklist(verdicts) -> bool:
    if not verdicts:
        typer_echo("✗ no requirements extracted")
        return False

    v = verdicts[0]
    checks: list[tuple[str, bool, str]] = []

    checks.append(
        ("understands requirements?", bool(v.requirement.behavior.prohibited_action),
         f"{v.requirement.id} extracted, schema valid")
    )
    has_mut = v.mutation is not None
    checks.append(
        ("creates meaningful mutations?", has_mut,
         f"{v.mutation.mutation_type.value}, 6/6 gates passed" if has_mut else "no mutation planned")
    )
    ran = v.result is not None and v.result.baseline is not None
    checks.append(
        ("runs tests?", ran,
         (f"{v.result.baseline.passed} passed, exit {v.result.baseline.exit_code}, "
          f"{v.result.baseline.duration_seconds:.2f}s") if ran else "no test run")
    )
    distinguishes = v.result is not None and v.result.survived is not None
    checks.append(
        ("distinguishes survive/kill?", distinguishes,
         ("SURVIVED" if v.result and v.result.survived else "KILLED") if distinguishes else "inconclusive")
    )
    repaired = v.repair is not None
    checks.append(
        ("repairs a surviving case?", repaired or v.status == "PROTECTED",
         v.repair.test_path if repaired else ("n/a — mutation was killed" if v.status == "PROTECTED" else "no repair"))
    )
    verified = v.verification is not None and v.verification.verified
    checks.append(
        ("verifies the repair?", verified or v.status == "PROTECTED",
         "passes on original, fails on mutant" if verified else
         ("n/a — mutation was killed" if v.status == "PROTECTED" else "not verified"))
    )

    all_ok = True
    for i, (label, ok, detail) in enumerate(checks, 1):
        mark = "✓" if ok else "✗"
        typer_echo(f"{i}. {label:<32} {mark}  {detail}")
        all_ok = all_ok and ok
    typer_echo("")
    typer_echo("READY" if all_ok else "NOT READY")
    return all_ok


def typer_echo(msg: str) -> None:
    import typer

    typer.echo(msg)


# ── trajectory rendering (Doc 3 section 12) ───────────────────────────────
#
# Reads reqguard.tracing.trajectory's JSONL output and renders the
# "reasoning -> tool -> observation -> adaptation -> verification" arc Doc 1
# section 27 describes -- the representative trajectory a submission needs.
# Read-only consumer of that module's public read_events(); never imports or
# modifies reqguard.tracing.trajectory's writing side (Track C's, crew-owned).


def _find_trajectory_file(path: str | Path) -> Path:
    """`path` may be a direct .jsonl file or a directory containing one
    (a report dir, e.g. ./reqguard-report or a benchmark case's report)."""
    p = Path(path)
    if p.is_file():
        return p
    if p.is_dir():
        candidate = p / "trajectory.jsonl"
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"no trajectory.jsonl in {p}")
    raise FileNotFoundError(f"no such file or directory: {p}")


def _event_line(ev: dict[str, Any], *, md: bool) -> str | None:
    """One rendered line for one event, or None to skip it (api_call is
    folded into the cost summary instead of shown per-line -- Doc 3 section
    11.2's own discipline: cost is summed from records, not narrated one by
    one)."""
    t = ev.get("type")
    stage = ev.get("stage")
    tag = f"[{stage}] " if stage else ""

    if t == "decision":
        rationale = ev.get("rationale", "")
        return f"- **{tag}Decision:** {rationale}" if md else f"  {tag}Decision: {rationale}"

    if t == "tool_call":
        tool = ev.get("tool", "?")
        inp = ev.get("input", {})
        return (f"- {tag}Called `{tool}`({inp})" if md
                else f"  {tag}Called {tool}({inp})")

    if t == "tool_result":
        tool = ev.get("tool", "?")
        summary = ev.get("result_summary", "")
        return (f"- {tag}`{tool}` → {summary}" if md
                else f"  {tag}{tool} -> {summary}")

    if t == "verdict":
        payload = ev.get("payload", {})
        rendered = ", ".join(f"{k}={v}" for k, v in payload.items())
        return (f"- **{tag}Verdict:** {rendered}" if md
                else f"  {tag}Verdict: {rendered}")

    if t == "error":
        kind = ev.get("kind", "?")
        message = ev.get("message", "")
        return (f"- ⚠ {tag}Error ({kind}): {message}" if md
                else f"  ! {tag}Error ({kind}): {message}")

    return None  # api_call and any unrecognized future type: folded elsewhere


def _cost_summary(events: list[dict[str, Any]]) -> dict[str, int]:
    """Sum usage across every api_call record. Never estimated (section
    11.2) -- if there are no api_call events (an all-replay run), every
    total is honestly 0, not omitted."""
    totals = {
        "api_calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }
    for ev in events:
        if ev.get("type") != "api_call":
            continue
        totals["api_calls"] += 1
        usage = ev.get("usage") or {}
        for key in ("input_tokens", "output_tokens",
                    "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                totals[key] += int(value)
    return totals


def render_trajectory(path: str | Path, fmt: str = "text") -> str:
    """Render one run's trajectory.jsonl as the representative narrative
    Doc 1 section 27 describes. `fmt` is "text" (terminal-friendly) or "md"
    (ready to paste into a submission)."""
    from reqguard.tracing.trajectory import read_events

    traj_path = _find_trajectory_file(path)
    events = list(read_events(traj_path))
    md = fmt == "md"

    if not events:
        return (
            f"# Trajectory: {traj_path}\n\n(no events recorded — an empty run, "
            "or every requirement was filtered out before any stage ran)\n"
            if md else
            f"Trajectory: {traj_path}\n"
            "(no events recorded — an empty run, or every requirement was "
            "filtered out before any stage ran)\n"
        )

    # Group by requirement_id, preserving first-appearance order. None (no
    # requirement attributed) sorts last, under its own heading, rather than
    # being silently dropped.
    order: list[str | None] = []
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for ev in events:
        rid = ev.get("requirement_id")
        if rid not in groups:
            groups[rid] = []
            order.append(rid)
    for ev in events:
        groups[ev.get("requirement_id")].append(ev)
    if None in groups:
        order.remove(None)
        order.append(None)

    lines: list[str] = []
    title = f"# Trajectory: {traj_path.parent.name or traj_path}" if md else f"Trajectory: {traj_path}"
    lines.append(title)
    lines.append("")

    for rid in order:
        heading = rid if rid is not None else "(orchestration — no requirement attributed)"
        lines.append(f"## {heading}" if md else f"-- {heading} --")
        for ev in groups[rid]:
            rendered = _event_line(ev, md=md)
            if rendered is not None:
                lines.append(rendered)
        lines.append("")

    cost = _cost_summary(events)
    if md:
        lines.append("## Cost")
        lines.append(f"- API calls: {cost['api_calls']}")
        lines.append(f"- Input tokens: {cost['input_tokens']:,}")
        lines.append(f"- Output tokens: {cost['output_tokens']:,}")
        lines.append(f"- Cache read tokens: {cost['cache_read_input_tokens']:,}")
        lines.append(f"- Cache creation tokens: {cost['cache_creation_input_tokens']:,}")
    else:
        lines.append("-- Cost --")
        lines.append(
            f"  api_calls={cost['api_calls']}  input_tokens={cost['input_tokens']}  "
            f"output_tokens={cost['output_tokens']}  "
            f"cache_read={cost['cache_read_input_tokens']}  "
            f"cache_creation={cost['cache_creation_input_tokens']}"
        )
    lines.append("")

    return "\n".join(lines)
