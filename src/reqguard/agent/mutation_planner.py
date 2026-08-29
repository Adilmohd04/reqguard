"""Stage 3 — Mutation Planner. Owned by Track B.

The highest-leverage stage in the system. Runs at `xhigh` effort. Produces a
`MutationSpec` — a minimal, plausible code change that violates a stated
requirement — that must pass all six of Track A's gates (`tools/mutation.py`)
on the first try, most of the time.

Reference: docs/03-implementation-manual.md sections 5.4, 6.1, 6.2, 10.2.

A single structured call (`messages.parse`), no tools: the investigation
already put the relevant code in context.
"""

from __future__ import annotations

from pathlib import Path

from reqguard.errors import AgentError
from reqguard.models import Investigation, MutationSpec, MutationType, Requirement
from reqguard.agent import client, prompts
from reqguard.agent.client import Trace

__all__ = ["run"]

_STAGE = "plan"

_MAX_TEST_CHARS = 12_000


def _read_tests(sandbox: Path) -> str:
    """The project's test sources, so the planner can see what IS and ISN'T
    covered. Truncated defensively — the point is the shape of the coverage,
    not every line."""
    chunks: list[str] = []
    total = 0
    for p in sorted(sandbox.rglob("test_*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            body = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = p.relative_to(sandbox).as_posix()
        chunk = f"# --- {rel} ---\n{body}"
        if total + len(chunk) > _MAX_TEST_CHARS:
            chunks.append(f"# --- {rel} --- (omitted: budget)")
            continue
        chunks.append(chunk)
        total += len(chunk)
    return "\n\n".join(chunks) if chunks else "(no test files found)"


def _user_turn(sandbox: Path, req: Requirement, inv: Investigation) -> str:
    b = req.behavior
    parts = [
        "Design ONE mutation that violates this requirement. Emit a MutationSpec.",
        "",
        f"requirement_id: {req.id}",
        f"statement: {req.statement}",
        f"category: {req.category.value}",
        "",
        "behavior triple:",
        f"  precondition:      {b.precondition}",
        f"  prohibited_action: {b.prohibited_action}",
        f"  expected_result:   {b.expected_result}",
        "",
        "enforcement sites found in Stage 2:",
    ]
    if inv.sites:
        for i, s in enumerate(inv.sites, 1):
            parts += [
                f"  [{i}] {s.file}  ::  {s.symbol}  (lines {s.start_line}-{s.end_line}, "
                f"confidence {s.confidence})",
                "      enforcement_snippet:",
                *(f"        {line}" for line in s.enforcement_snippet.splitlines()),
            ]
    else:
        parts.append("  (none — Stage 2 found no enforcement point)")
    if inv.domain_notes:
        parts += ["", f"domain notes: {inv.domain_notes}"]
    parts += [
        "",
        "existing tests (the gap is usually that none of these builds the "
        "precondition state):",
        _read_tests(sandbox),
        "",
        "Copy `original_snippet` VERBATIM from the target file — it must appear "
        "exactly once. Keep the diff <= 15 lines. The mutated code must still "
        "parse.",
    ]
    return "\n".join(parts)


def run(
    sandbox: str | Path,
    requirement: Requirement,
    investigation: Investigation,
    trace: Trace | None = None,
) -> MutationSpec:
    """Plan a single requirement-violating mutation for ``requirement``."""
    sandbox = Path(sandbox)
    spec = client.structured(
        _STAGE,
        prompts.load(_STAGE),
        _user_turn(sandbox, requirement, investigation),
        MutationSpec,
        trace,
    )
    # semantic_change is the escape hatch from the fixed catalogue; the manual
    # (sections 5.4, 6.2) only permits it with a written justification. Enforce
    # that here as well as in the prompt -- a mislabelled semantic_change with no
    # rationale would otherwise slip into the per-category metrics.
    if spec.mutation_type is MutationType.SEMANTIC_CHANGE and not spec.rationale.strip():
        raise AgentError(
            "mutation_planner: semantic_change requires a non-empty rationale"
        )
    return spec
