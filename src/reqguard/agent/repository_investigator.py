"""Stage 2 — Repository Investigator. Owned by Track B.

Locates where a single requirement is enforced in a codebase, using the seven
read-only tools from Track A (bound in `tools/registry.py`). Driven by the SDK
Tool Runner, ending in a validated `Investigation`.

Reference: docs/03-implementation-manual.md sections 4.3, 5.3, 10.2.

The orchestrator calls `run(sandbox, requirement, trace)` once per requirement.
"""

from __future__ import annotations

from pathlib import Path

from reqguard.models import Investigation, Requirement
from reqguard.agent import client, prompts
from reqguard.agent.client import Trace
from reqguard.tools import registry

__all__ = ["run", "REQGUARD_MD"]

_STAGE = "investigate"

# Optional project-specific context (Doc 1 section 18). Appended to the user
# turn only — never the cached system prefix (section 5).
REQGUARD_MD = "REQGUARD.md"


def _user_turn(sandbox: Path, req: Requirement) -> str:
    b = req.behavior
    parts = [
        "Investigate this ONE requirement. Return an Investigation for it.",
        "",
        f"requirement_id: {req.id}",
        f"statement: {req.statement}",
        f"category: {req.category.value}   severity: {req.severity.value}",
        f"source_location: {req.source_location}",
        "",
        "behavior triple:",
        f"  precondition:      {b.precondition}",
        f"  prohibited_action: {b.prohibited_action}",
        f"  expected_result:   {b.expected_result}",
    ]
    extra = sandbox / REQGUARD_MD
    if extra.is_file():
        text = extra.read_text(encoding="utf-8").strip()
        if text:
            parts += ["", "--- project context (REQGUARD.md) ---", text]
    return "\n".join(parts)


def run(
    sandbox: str | Path,
    requirement: Requirement,
    trace: Trace | None = None,
) -> Investigation:
    """Find every site that enforces (or should enforce) ``requirement``.

    Returns an ``Investigation`` with an empty ``sites`` list when no enforcement
    point exists — the model is instructed not to invent one, and this function
    does not second-guess that.
    """
    sandbox = Path(sandbox)
    tools = registry.build_read_tools(sandbox)
    result = client.run_with_tools(
        _STAGE,
        prompts.load(_STAGE),
        _user_turn(sandbox, requirement),
        tools,
        Investigation,
        trace,
    )
    return result
