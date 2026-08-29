"""Orchestrator — wire Stages 1-6 into one analysis run. Owned by the Supervisor.

Reference: docs/03-implementation-manual.md section 10.

The stage split (section 10.1): stages 1, 2, 3, 5 are model calls; stages 4
(execute), 6 (verify), 7 (report) are pure Python. The model is never handed a
tool that returns a verdict or writes to the user's repository.

The loop below (section 10.2) is meant to be read top to bottom — every
``continue`` carries a named inconclusive reason. That readability is the point:
this function is what a reviewer actually reads.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from reqguard.config import Config, load_config
from reqguard.errors import AgentError, MutationRejected, ReqGuardInternalError
from reqguard.models import (
    Investigation,
    MutationResult,
    MutationSpec,
    Requirement,
    RepairProposal,
    RunStatus,
    TestOutcome,
    Verdict,
    Verification,
)
from reqguard.agent import (
    mutation_planner,
    replay as replay_mod,
    repository_investigator,
    requirement_analyzer,
    test_repairer,
)
from reqguard.agent.policy import MODEL
from reqguard.sandbox import sandbox
from reqguard.tools import mutation
from reqguard.tools.test_runner import run_tests
from reqguard.verification.verifier import verify

__all__ = ["analyze", "TrajectoryTrace"]


class TrajectoryTrace:
    """Adapts Track C's :class:`~reqguard.tracing.trajectory.Trajectory` to the
    ``trace.api_call(stage, response)`` shape that ``agent/client.py`` calls,
    plus the ``tool_result`` / ``decision`` / ``verdict`` events ``_run_one``
    and ``_repair_loop`` emit at each stage boundary below.

    ``client.py`` never imports Track C; this is the seam. Set
    :attr:`requirement_id` before each requirement so api_call records are
    attributed and the section 11.3 cache-miss alarm is scoped correctly.

    This is the ONLY thing that makes a run's trajectory a representative
    narrative (Doc 1 section 27) rather than a bare list of API calls (live
    mode) or nothing at all (replay mode, where no live call ever happens so
    ``api_call`` never fires either). ``decision`` calls pass through each
    stage model's own ``rationale`` field verbatim -- a genuine model-authored
    sentence, in both live and replayed runs, never text the orchestrator
    invents -- keeping intact the same "agent proposes, orchestrator never
    asserts" boundary the whole design rests on. ``verdict`` calls carry only
    computed facts (exit codes, booleans), the same discipline as the Verdict
    objects this function returns.
    """

    def __init__(self, trajectory: Any | None = None) -> None:
        self._traj = trajectory
        self.requirement_id: str | None = None

    def bind(self, requirement_id: str | None) -> None:
        self.requirement_id = requirement_id

    def api_call(self, stage: str, response: Any) -> None:
        if self._traj is None:
            return
        rid = getattr(response, "_request_id", None) or getattr(response, "id", None)
        usage = getattr(response, "usage", None)
        with contextlib.suppress(Exception):
            self._traj.api_call(
                model=MODEL,
                request_id=rid,
                usage=usage,
                requirement_id=self.requirement_id,
                stage=stage,
            )

    def tool_result(self, tool: str, result_summary: str, *, stage: str | None = None) -> None:
        if self._traj is not None:
            with contextlib.suppress(Exception):
                self._traj.tool_result(
                    tool, result_summary, requirement_id=self.requirement_id, stage=stage
                )

    def decision(self, rationale: str, *, stage: str | None = None) -> None:
        if self._traj is not None:
            with contextlib.suppress(Exception):
                self._traj.decision(
                    rationale, requirement_id=self.requirement_id, stage=stage
                )

    def verdict(self, payload: dict, *, stage: str | None = None) -> None:
        if self._traj is not None:
            with contextlib.suppress(Exception):
                self._traj.verdict(
                    payload, requirement_id=self.requirement_id, stage=stage
                )


# ── verdict constructors ─────────────────────────────────────────────────────

def _inconclusive(req: Requirement, reason: str, **extra: Any) -> Verdict:
    return Verdict(
        requirement=req,
        status="INCONCLUSIVE",
        inconclusive_reason=reason,
        **extra,
    )


def _mutation_result(
    spec: MutationSpec, base: TestOutcome, mutated: TestOutcome, diff: str, survived: bool | None
) -> MutationResult:
    return MutationResult(
        mutation_id=spec.id, baseline=base, mutated=mutated, diff=diff, survived=survived
    )


def _protected(
    req: Requirement,
    inv: Investigation,
    spec: MutationSpec,
    base: TestOutcome,
    mutated: TestOutcome,
    diff: str,
) -> Verdict:
    return Verdict(
        requirement=req,
        status="PROTECTED",
        investigation=inv,
        mutation=spec,
        result=_mutation_result(spec, base, mutated, diff, survived=False),
    )


def _gap(
    req: Requirement,
    inv: Investigation,
    spec: MutationSpec,
    base: TestOutcome,
    mutated: TestOutcome,
    diff: str,
    repair: RepairProposal | None,
    ver: Verification | None,
) -> Verdict:
    status = "REPAIRED" if (ver is not None and ver.verified) else "INSUFFICIENTLY_PROTECTED"
    return Verdict(
        requirement=req,
        status=status,
        investigation=inv,
        mutation=spec,
        result=_mutation_result(spec, base, mutated, diff, survived=True),
        repair=repair,
        verification=ver,
    )


# ── the loop ─────────────────────────────────────────────────────────────────

def analyze(
    repo: str | Path,
    cfg: Config | None = None,
    trajectory: Any | None = None,
    *,
    only: str | None = None,
    report_dir: Path | None = None,
    replay_dir: str | Path | None = None,
    record_dir: str | Path | None = None,
) -> list[Verdict]:
    """Run ReqGuard end to end over ``repo`` and return one Verdict per
    requirement.

    ``only`` restricts the run to a single requirement id (the demo path).
    ``report_dir`` receives per-requirement ``mutations/*.diff`` and
    ``repairs/*.py`` — repairs are written there, **never to the user's repo**
    (section 12).

    ``replay_dir`` (or ``$REQGUARD_REPLAY``) swaps every LLM stage for recorded
    outputs — no key, no spend, deterministic. ``record_dir`` captures a live
    run's stage outputs so it can be replayed later.
    """
    repo = Path(repo).resolve()
    cfg = cfg or load_config(repo)
    trace = TrajectoryTrace(trajectory)
    case = repo.name
    rdir = replay_mod.resolve_dir(replay_dir)
    rec = Path(record_dir) if record_dir else None

    if rdir is not None:
        requirements = replay_mod.requirements(rdir, case)
    else:
        requirements = requirement_analyzer.run(repo / cfg.requirements_path, trace)
    if rec is not None:
        _record(rec, case, "analyze", [r.model_dump(mode="json") for r in requirements])
    if only:
        requirements = [r for r in requirements if r.id == only]
    requirements = requirements[: cfg.max_mutations]

    verdicts: list[Verdict] = []
    for req in requirements:
        trace.bind(req.id)
        try:
            verdicts.append(_run_one(repo, cfg, req, trace, report_dir, case, rdir, rec))
        except ReqGuardInternalError:
            raise
        except AgentError as exc:
            verdicts.append(_inconclusive(req, f"agent error: {exc}"))
        except Exception as exc:  # noqa: BLE001 - one bad requirement must not kill the run
            verdicts.append(_inconclusive(req, f"unexpected error: {exc!r}"))
    return verdicts


def _record(rec: Path, case: str, name: str, payload: Any) -> None:
    d = rec / case
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_one(
    repo: Path,
    cfg: Config,
    req: Requirement,
    trace: TrajectoryTrace,
    report_dir: Path | None,
    case: str = "",
    replay_dir: Path | None = None,
    record_dir: Path | None = None,
) -> Verdict:
    with sandbox(repo, cfg) as orig_sb, sandbox(repo, cfg) as mut_sb:
        orig_sb, mut_sb = Path(orig_sb), Path(mut_sb)

        base = run_tests(orig_sb, cfg.test_command, cfg.test_timeout_s)
        if base.exit_code != 0:
            trace.verdict({"stopped": "INCONCLUSIVE", "reason": "suite not green before mutation"}, stage="execute")
            return _inconclusive(
                req,
                "suite not green before mutation; downstream signals would be "
                f"uninterpretable ({base.status.value}, exit {base.exit_code})",
            )
        trace.tool_result(
            "run_tests",
            f"baseline: {base.passed} passed, {base.failed} failed, exit {base.exit_code}",
            stage="execute",
        )

        if replay_dir is not None:
            inv = replay_mod.investigation(replay_dir, case, req.id)
        else:
            inv = repository_investigator.run(orig_sb, req, trace)
        if record_dir is not None:
            _record(record_dir, case, f"{req.id}.investigate", inv.model_dump(mode="json"))
        if inv.rationale:
            trace.decision(inv.rationale, stage="investigate")
        if not inv.sites:
            trace.verdict({"stopped": "INCONCLUSIVE", "reason": "no enforcement site found"}, stage="investigate")
            return _inconclusive(req, "no enforcement site found for the requirement", investigation=inv)

        if replay_dir is not None:
            spec = replay_mod.mutation_spec(replay_dir, case, req.id)
        else:
            spec = mutation_planner.run(orig_sb, req, inv, trace)
        if record_dir is not None:
            _record(record_dir, case, f"{req.id}.plan", spec.model_dump(mode="json"))
        if spec.rationale:
            trace.decision(spec.rationale, stage="plan")
        try:
            diff = mutation.apply_with_gates(spec, mut_sb, max_diff_lines=cfg.max_diff_lines)
        except MutationRejected as exc:
            trace.verdict(
                {"stopped": "INCONCLUSIVE", "reason": f"mutation rejected at gate {exc.gate!r}"}, stage="plan"
            )
            return _inconclusive(
                req, f"mutation rejected at gate {exc.gate!r}: {exc.detail}", investigation=inv, mutation=spec
            )
        trace.tool_result(
            "apply_mutation", f"{spec.mutation_type.value} on {spec.target_file}::{spec.target_symbol}",
            stage="plan",
        )

        if report_dir is not None:
            _write_diff(report_dir, req.id, diff)

        mutated = run_tests(mut_sb, cfg.test_command, cfg.test_timeout_s)
        if mutated.status is not RunStatus.OK:
            trace.verdict(
                {"stopped": "INCONCLUSIVE", "reason": f"mutated run inconclusive: {mutated.reason}"},
                stage="execute",
            )
            return _inconclusive(
                req, f"mutated run inconclusive: {mutated.reason}", investigation=inv, mutation=spec
            )
        trace.tool_result(
            "run_tests",
            f"mutated: {mutated.passed} passed, {mutated.failed} failed, exit {mutated.exit_code}",
            stage="execute",
        )
        survived = mutated.exit_code == 0
        trace.verdict({"survived": survived}, stage="execute")

        if not survived:  # KILLED — the suite caught it
            return _protected(req, inv, spec, base, mutated, diff)

        # SURVIVED — the finding. Try to close it.
        repair, ver = _repair_loop(
            cfg, req, spec, mutated, orig_sb, mut_sb, trace, report_dir, case, replay_dir, record_dir
        )
        return _gap(req, inv, spec, base, mutated, diff, repair, ver)


def _repair_loop(
    cfg: Config,
    req: Requirement,
    spec: MutationSpec,
    mutated: TestOutcome,
    orig_sb: Path,
    mut_sb: Path,
    trace: TrajectoryTrace,
    report_dir: Path | None,
    case: str = "",
    replay_dir: Path | None = None,
    record_dir: Path | None = None,
) -> tuple[RepairProposal | None, Verification | None]:
    prior: list[tuple[RepairProposal, str]] = []
    last_proposal: RepairProposal | None = None
    last_ver: Verification | None = None

    for attempt in range(cfg.max_repair_attempts + 1):
        if replay_dir is not None:
            proposal = replay_mod.repair(replay_dir, case, req.id, attempt)
        else:
            proposal = test_repairer.run(
                orig_sb, req, spec, mutated, None, prior_failures=prior
            )
        if record_dir is not None:
            suffix = "" if attempt == 0 else f".{attempt}"
            _record(record_dir, case, f"{req.id}.repair{suffix}", proposal.model_dump(mode="json"))
        if proposal.rationale:
            trace.decision(proposal.rationale, stage="repair")
        last_proposal = proposal

        # Materialize the candidate somewhere neutral; verifier injects it into
        # each sandbox itself. Never write into the user's repo.
        scratch = (report_dir or mut_sb.parent) / f"_candidate_{req.id}.py"
        scratch.write_text(proposal.test_source, encoding="utf-8")
        try:
            ver = verify(scratch, orig_sb, mut_sb, timeout_s=cfg.test_timeout_s)
        finally:
            with contextlib.suppress(OSError):
                scratch.unlink()
        last_ver = ver
        trace.verdict(
            {
                "verified": ver.verified,
                "passes_on_original": ver.passes_on_original,
                "kills_mutant": ver.kills_mutant,
            },
            stage="repair",
        )

        if ver.verified:
            if report_dir is not None:
                _write_repair(report_dir, req.id, proposal)
            return proposal, ver

        prior.append((proposal, ver.failure_reason or "rejected"))

    # exhausted retries — honest partial result
    if report_dir is not None and last_proposal is not None:
        _write_repair(report_dir, req.id, last_proposal, verified=False)
    return last_proposal, last_ver


def _write_diff(report_dir: Path, req_id: str, diff: str) -> None:
    d = report_dir / "mutations"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{req_id}.diff").write_text(diff, encoding="utf-8")


def _write_repair(
    report_dir: Path, req_id: str, proposal: RepairProposal, *, verified: bool = True
) -> None:
    d = report_dir / "repairs"
    d.mkdir(parents=True, exist_ok=True)
    header = "" if verified else "# NOTE: this repair did NOT verify — kept for reference only\n"
    (d / f"{req_id}.py").write_text(header + proposal.test_source, encoding="utf-8")
