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
    ``trace.api_call(stage, response)`` shape that ``agent/client.py`` calls.

    ``client.py`` never imports Track C; this is the seam. Set
    :attr:`requirement_id` before each requirement so api_call records are
    attributed and the section 11.3 cache-miss alarm is scoped correctly.
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

    # passthroughs the stages don't call today but the orchestrator may
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
            return _inconclusive(
                req,
                "suite not green before mutation; downstream signals would be "
                f"uninterpretable ({base.status.value}, exit {base.exit_code})",
            )

        if replay_dir is not None:
            inv = replay_mod.investigation(replay_dir, case, req.id)
        else:
            inv = repository_investigator.run(orig_sb, req, trace)
        if record_dir is not None:
            _record(record_dir, case, f"{req.id}.investigate", inv.model_dump(mode="json"))
        if not inv.sites:
            return _inconclusive(req, "no enforcement site found for the requirement", investigation=inv)

        if replay_dir is not None:
            spec = replay_mod.mutation_spec(replay_dir, case, req.id)
        else:
            spec = mutation_planner.run(orig_sb, req, inv, trace)
        if record_dir is not None:
            _record(record_dir, case, f"{req.id}.plan", spec.model_dump(mode="json"))
        try:
            diff = mutation.apply_with_gates(spec, mut_sb, max_diff_lines=cfg.max_diff_lines)
        except MutationRejected as exc:
            return _inconclusive(
                req, f"mutation rejected at gate {exc.gate!r}: {exc.detail}", investigation=inv, mutation=spec
            )

        if report_dir is not None:
            _write_diff(report_dir, req.id, diff)

        mutated = run_tests(mut_sb, cfg.test_command, cfg.test_timeout_s)
        if mutated.status is not RunStatus.OK:
            return _inconclusive(
                req, f"mutated run inconclusive: {mutated.reason}", investigation=inv, mutation=spec
            )

        if mutated.exit_code == 1:  # KILLED — the suite caught it
            return _protected(req, inv, spec, base, mutated, diff)

        # SURVIVED — the finding. Try to close it.
        repair, ver = _repair_loop(
            cfg, req, spec, mutated, orig_sb, mut_sb, report_dir, case, replay_dir, record_dir
        )
        return _gap(req, inv, spec, base, mutated, diff, repair, ver)


def _repair_loop(
    cfg: Config,
    req: Requirement,
    spec: MutationSpec,
    mutated: TestOutcome,
    orig_sb: Path,
    mut_sb: Path,
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
