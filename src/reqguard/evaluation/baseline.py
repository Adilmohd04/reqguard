"""The baseline control arm. Owned by Track B.

Same model, same effort (`xhigh`), same read-only tool surface, same Tool Runner
as ReqGuard. **Only the workflow differs**: the baseline is one agent asked the
question a competent developer would actually type — "does the existing suite
cover this requirement?" — with no mutation, execution, repair, or verification
loop behind it.

Reference: docs/03-implementation-manual.md sections 5.6, 16.

If ReqGuard wins, it must win on workflow. Handicapping this arm — a weaker
model, lower effort, fewer tools, or a padded prompt — invalidates the whole
experiment, and a judge finds it in thirty seconds. The prompt
(`prompts/baseline.md`) is deliberately short for the same reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from reqguard.models import Requirement
from reqguard.agent import client, prompts, replay as replay_mod
from reqguard.agent.client import Trace
from reqguard.tools import registry

__all__ = ["BaselineFinding", "assess", "run", "STAGE"]

STAGE = "baseline"


class BaselineFinding(BaseModel):
    """One baseline verdict per requirement, scored by the same evaluator
    (section 17) against the same ground truth as the ReqGuard arm."""

    requirement_id: str
    status: Literal["PROTECTED", "INSUFFICIENTLY_PROTECTED"]
    reasoning: str
    rationale: str = ""  # one sentence, for the trajectory (section 11)


def _user_turn(req: Requirement) -> str:
    b = req.behavior
    return "\n".join(
        [
            "Does the existing test suite adequately protect this requirement?",
            "Answer PROTECTED or INSUFFICIENTLY_PROTECTED for it.",
            "",
            f"requirement_id: {req.id}",
            f"statement: {req.statement}",
            f"category: {req.category.value}   severity: {req.severity.value}",
            "",
            "behavior triple:",
            f"  precondition:      {b.precondition}",
            f"  prohibited_action: {b.prohibited_action}",
            f"  expected_result:   {b.expected_result}",
        ]
    )


def assess(
    sandbox: str | Path,
    requirement: Requirement,
    trace: Trace | None = None,
    *,
    replay_dir: str | Path | None = None,
    case: str | None = None,
) -> BaselineFinding:
    """Assess a single requirement with the baseline agent.

    In replay mode (``replay_dir`` or ``$REQGUARD_REPLAY``) the recorded verdict
    is returned instead of calling the model.
    """
    sandbox = Path(sandbox)
    rdir = replay_mod.resolve_dir(replay_dir)
    if rdir is not None:
        return replay_mod.baseline_finding(rdir, case or sandbox.name, requirement.id)

    tools = registry.build_read_tools(sandbox)
    finding = client.run_with_tools(
        STAGE,
        prompts.load(STAGE),
        _user_turn(requirement),
        tools,
        BaselineFinding,
        trace,
    )
    return finding


def run(
    sandbox: str | Path,
    requirements: list[Requirement],
    trace: Trace | None = None,
    *,
    replay_dir: str | Path | None = None,
    case: str | None = None,
) -> list[BaselineFinding]:
    """Assess every requirement. One call per requirement so the cached system
    prefix is reused across them (section 4.3)."""
    return [
        assess(sandbox, req, trace, replay_dir=replay_dir, case=case)
        for req in requirements
    ]
