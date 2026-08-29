"""ReqGuard data contracts.

FROZEN INTERFACE — owned by the Supervisor (crew role S).

Every stage boundary in ReqGuard is one of these models. No raw dict crosses a
module boundary. Track A, B, and C all import from here and none of them may
edit this file: a change here invalidates work in all three worktrees at once.

To propose a change: open a CONTRACT CHANGE request per docs/04-build-crew-protocol.md
section 6. The Supervisor edits, re-freezes, and broadcasts. Never edit in a
track worktree.

Reference: docs/03-implementation-manual.md section 3.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "Severity", "Category", "MutationType", "RunStatus",
    "Behavior", "Requirement",
    "Site", "Investigation",
    "MutationSpec", "TestOutcome", "MutationResult",
    "RepairProposal", "Verification", "Verdict",
]


# ── Enumerations ──────────────────────────────────────────────────────────

class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    """Requirement categories. Mirrors the benchmark composition table
    (docs/03-implementation-manual.md section 15.3)."""
    AUTHORIZATION = "authorization"
    STATE_TRANSITION = "state_transition"
    BUSINESS_RULE = "business_rule"
    VALIDATION = "validation"
    API_ERROR = "api_error"
    SEMANTIC = "semantic"


class MutationType(str, Enum):
    """The constrained catalogue (Doc 2 section 11) plus one escape hatch.

    SEMANTIC_CHANGE exists for domain-meaning violations no catalogue entry can
    name -- e.g. reporting an 80-of-100 order as completed. It requires a
    non-empty `rationale`; see the plausibility table in Doc 3 section 6.2.
    """
    REMOVE_GUARD = "remove_guard"
    COMPARISON_CHANGE = "comparison_change"
    BOOLEAN_INVERSION = "boolean_inversion"
    BOUNDARY_SHIFT = "boundary_shift"
    ALLOW_TRANSITION = "allow_transition"
    REMOVE_VALIDATION = "remove_validation"
    SEMANTIC_CHANGE = "semantic_change"


class RunStatus(str, Enum):
    """Doc 2 section 33: an environment failure must never be scored as a
    mutation kill. Only OK yields a survival verdict."""
    OK = "ok"
    ENVIRONMENT_ERROR = "environment_error"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"


# ── Stage 1: Requirement Analyzer ─────────────────────────────────────────

class Behavior(BaseModel):
    precondition: str        # "order.status == cancelled"
    prohibited_action: str   # "ship(order)" -- a concrete operation, not prose
    expected_result: str     # "operation rejected"


class Requirement(BaseModel):
    id: str                  # REQ-007
    statement: str           # VERBATIM from the source document
    behavior: Behavior
    category: Category
    severity: Severity
    source_location: str     # "requirements.md:L42"


# ── Stage 2: Repository Investigator ──────────────────────────────────────

class Site(BaseModel):
    file: str                # repo-relative
    symbol: str              # "OrderService.ship_order"
    start_line: int
    end_line: int
    enforcement_snippet: str  # the lines that actually enforce the requirement
    confidence: Literal["high", "medium", "low"]


class Investigation(BaseModel):
    requirement_id: str
    sites: list[Site] = Field(default_factory=list)
    covering_tests: list[str] = Field(default_factory=list)  # pytest node ids
    domain_notes: str = ""
    rationale: str = ""      # ONE sentence, for the trajectory (Doc 2 section 29)


# ── Stage 3: Mutation Planner ─────────────────────────────────────────────

class MutationSpec(BaseModel):
    id: str
    requirement_id: str
    mutation_type: MutationType
    target_file: str          # repo-relative
    target_symbol: str
    original_snippet: str     # MUST match the file exactly once
    mutated_snippet: str
    description: str          # "Remove cancelled-order validation"
    expected_violation: str   # "Cancelled order can be shipped"
    violation_scenario: str   # concrete inputs/state that expose it
    rationale: str = ""       # ONE sentence, for the trajectory


# ── Stage 4: Execution ────────────────────────────────────────────────────

class TestOutcome(BaseModel):
    status: RunStatus
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    failing_node_ids: list[str] = Field(default_factory=list)
    stdout_tail: str = ""
    reason: str | None = None   # populated when status is not OK


class MutationResult(BaseModel):
    mutation_id: str
    baseline: TestOutcome       # unmutated -- MUST be green (Doc 3 section 8.3)
    mutated: TestOutcome
    diff: str
    survived: bool | None       # None when inconclusive


# ── Stage 5/6: Repair and Verification ────────────────────────────────────

class RepairProposal(BaseModel):
    requirement_id: str
    mutation_id: str
    test_path: str
    test_source: str
    explanation: str
    rationale: str = ""


class Verification(BaseModel):
    """Both conditions are load-bearing (Doc 2 section 14).

    kills_mutant alone is satisfied by `assert False`, which kills every mutant
    and repairs nothing. passes_on_original alone proves the test is valid but
    not that it detects anything.
    """
    passes_on_original: bool    # condition A
    kills_mutant: bool          # condition B
    verified: bool              # A and B
    failure_reason: str | None = None


# ── Final output ──────────────────────────────────────────────────────────

class Verdict(BaseModel):
    requirement: Requirement
    status: Literal[
        "PROTECTED",
        "INSUFFICIENTLY_PROTECTED",
        "REPAIRED",
        "INCONCLUSIVE",
    ]
    investigation: Investigation | None = None
    mutation: MutationSpec | None = None
    result: MutationResult | None = None
    repair: RepairProposal | None = None
    verification: Verification | None = None
    inconclusive_reason: str | None = None
