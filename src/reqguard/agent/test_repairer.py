"""Stage 5 — Test Repairer. Owned by Track B.

A requirement violation survived the existing suite. This stage writes the
missing test: one focused test that PASSES on the correct implementation and
FAILS on the mutant. Driven by the Tool Runner + read tools so it can match the
target suite's fixture and import conventions.

Reference: docs/03-implementation-manual.md sections 5.5, 9, 10.2.

`run` accepts `prior_failures` — (proposal, verifier failure_reason) pairs from
earlier attempts — so the orchestrator can re-prompt with the specific reason a
previous test was rejected (section 9). The verifier stays the judge; this
module never decides `verified`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from reqguard.models import MutationSpec, RepairProposal, Requirement, TestOutcome
from reqguard.agent import client, prompts
from reqguard.agent.client import Trace
from reqguard.tools import registry

__all__ = ["run"]

_STAGE = "repair"


def _user_turn(
    req: Requirement,
    spec: MutationSpec,
    mutated: TestOutcome,
    prior_failures: Sequence[tuple[RepairProposal, str]],
) -> str:
    b = req.behavior
    parts = [
        "Write the missing test. Emit a RepairProposal.",
        "",
        f"requirement_id: {req.id}",
        f"statement: {req.statement}",
        "",
        "behavior triple:",
        f"  precondition:      {b.precondition}",
        f"  prohibited_action: {b.prohibited_action}",
        f"  expected_result:   {b.expected_result}",
        "",
        f"mutation_id: {spec.id}",
        f"mutation_type: {spec.mutation_type.value}",
        f"target: {spec.target_file}  ::  {spec.target_symbol}",
        f"expected_violation: {spec.expected_violation}",
        f"violation_scenario: {spec.violation_scenario}",
        "",
        "mutation diff (original -> mutated snippet):",
        "  --- original",
        *(f"  {line}" for line in spec.original_snippet.splitlines()),
        "  +++ mutated",
        *(f"  {line}" for line in spec.mutated_snippet.splitlines()),
        "",
        "test output from the mutated run (the suite still passed — that is the gap):",
        f"  status={mutated.status.value} exit={mutated.exit_code} "
        f"passed={mutated.passed} failed={mutated.failed}",
        f"  stdout tail: {mutated.stdout_tail[-800:]}" if mutated.stdout_tail else "",
        "",
        "Use the read tools to study the existing test files and match their "
        "fixtures, imports, naming, and assertion style. Build the precondition "
        "state the requirement names. `test_source` must be the COMPLETE file.",
    ]
    for i, (proposal, reason) in enumerate(prior_failures, 1):
        parts += [
            "",
            f"--- attempt {i} was REJECTED by the verifier ---",
            f"reason: {reason}",
            f"rejected test_path: {proposal.test_path}",
            "rejected test_source:",
            *(f"  {line}" for line in proposal.test_source.splitlines()),
            "Fix the specific reason above. Do not repeat the same test.",
        ]
    return "\n".join(p for p in parts if p is not None)


def run(
    sandbox: str | Path,
    requirement: Requirement,
    spec: MutationSpec,
    mutated_outcome: TestOutcome,
    trace: Trace | None = None,
    *,
    prior_failures: Sequence[tuple[RepairProposal, str]] | None = None,
) -> RepairProposal:
    """Propose one repair test. Written to the report dir by the caller, never
    to the target repo (section 12)."""
    sandbox = Path(sandbox)
    tools = registry.build_read_tools(sandbox)
    proposal = client.run_with_tools(
        _STAGE,
        prompts.load(_STAGE),
        _user_turn(requirement, spec, mutated_outcome, prior_failures or ()),
        tools,
        RepairProposal,
        trace,
    )
    return proposal
