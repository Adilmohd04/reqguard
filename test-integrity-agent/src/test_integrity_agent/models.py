"""Core data structures.

These are plain dataclasses with explicit ``to_dict`` methods rather than
pydantic models: the package is a CLI tool and a library, and keeping the
dependency surface small matters more here than validation sugar.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MutantOutcome(str, Enum):
    """What happened when a mutant was run against the test suite."""

    KILLED = "killed"
    """The suite failed. The requirement is defended at this point."""

    SURVIVED = "survived"
    """The suite passed with broken code. This is the finding that matters."""

    TIMEOUT = "timeout"
    """The suite exceeded the timeout. Usually an induced infinite loop."""

    ERROR = "error"
    """The mutant could not be evaluated (patch did not apply, runner crashed)."""


class MutationSource(str, Enum):
    """Where a mutation came from."""

    LLM = "llm"
    """Requirement-directed: an LLM read the requirement and broke it on purpose."""

    AST = "ast"
    """Fallback: a mechanical AST operator, not requirement-aware."""


@dataclass(frozen=True)
class Requirement:
    """A single written requirement with a stable identifier."""

    id: str
    text: str
    source: str
    """Where it came from, e.g. ``REQUIREMENTS.md:12`` or ``mypkg/orders.py:place_order``."""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class SourceSpan:
    """A contiguous region of a source file that implements a requirement."""

    path: str
    """Repo-relative POSIX path."""
    start_line: int
    """1-indexed, inclusive."""
    end_line: int
    """1-indexed, inclusive."""
    symbol: str | None = None
    """Qualified name of the enclosing function/class, when known."""
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbol": self.symbol,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True)
class Mutation:
    """A concrete, validated edit that violates one requirement."""

    id: str
    requirement_id: str
    path: str
    original_source: str
    mutated_source: str
    explanation: str
    origin: MutationSource
    operator: str = ""
    """For AST mutations, the operator name (e.g. ``comparison-flip``)."""
    span: SourceSpan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "requirement_id": self.requirement_id,
            "path": self.path,
            "explanation": self.explanation,
            "origin": self.origin.value,
            "operator": self.operator,
            "span": self.span.to_dict() if self.span else None,
        }

    @staticmethod
    def make_id(requirement_id: str, path: str, mutated_source: str) -> str:
        payload = path + "\x00" + mutated_source
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]
        return f"{requirement_id}-{digest}"


@dataclass
class MutantResult:
    """The outcome of running the test suite against one mutant."""

    mutation: Mutation
    outcome: MutantOutcome
    duration_s: float
    returncode: int | None = None
    output_tail: str = ""
    diff: str = ""
    error: str = ""

    @property
    def survived(self) -> bool:
        return self.outcome is MutantOutcome.SURVIVED

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.mutation.to_dict())
        data.update(
            {
                "outcome": self.outcome.value,
                "duration_s": round(self.duration_s, 3),
                "returncode": self.returncode,
                "diff": self.diff,
                "output_tail": self.output_tail,
                "error": self.error,
            }
        )
        return data


@dataclass
class RequirementScore:
    """Aggregated results for one requirement."""

    requirement: Requirement
    generated: int = 0
    killed: int = 0
    survived: int = 0
    timed_out: int = 0
    errored: int = 0
    results: list[MutantResult] = field(default_factory=list)
    note: str = ""

    @property
    def evaluable(self) -> int:
        """Mutants that produced a usable kill/survive signal."""
        return self.killed + self.survived

    @property
    def score(self) -> float | None:
        """Fraction of evaluable mutants the suite caught, or ``None`` if no signal.

        Timeouts and errors are excluded from the denominator: a timeout tells
        you the suite noticed *something*, but not that it asserted anything,
        and an error tells you nothing at all.
        """
        if self.evaluable == 0:
            return None
        return self.killed / self.evaluable

    @property
    def protected(self) -> bool:
        """True only when every evaluable mutant was caught."""
        return self.evaluable > 0 and self.survived == 0

    @property
    def survivors(self) -> list[MutantResult]:
        return [r for r in self.results if r.survived]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement.to_dict(),
            "generated": self.generated,
            "killed": self.killed,
            "survived": self.survived,
            "timed_out": self.timed_out,
            "errored": self.errored,
            "evaluable": self.evaluable,
            "score": self.score,
            "protected": self.protected,
            "note": self.note,
            "mutants": [r.to_dict() for r in self.results],
        }


@dataclass
class ScanReport:
    """The full result of a scan."""

    repo: str
    model: str
    started_at: str
    finished_at: str
    duration_s: float
    scores: list[RequirementScore] = field(default_factory=list)
    llm_used: bool = True
    degraded_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    test_command: str = ""

    @property
    def total_generated(self) -> int:
        return sum(s.generated for s in self.scores)

    @property
    def total_killed(self) -> int:
        return sum(s.killed for s in self.scores)

    @property
    def total_survived(self) -> int:
        return sum(s.survived for s in self.scores)

    @property
    def total_timed_out(self) -> int:
        return sum(s.timed_out for s in self.scores)

    @property
    def total_errored(self) -> int:
        return sum(s.errored for s in self.scores)

    @property
    def suite_score(self) -> float | None:
        """Suite-level integrity: killed / (killed + survived) across all requirements."""
        evaluable = self.total_killed + self.total_survived
        if evaluable == 0:
            return None
        return self.total_killed / evaluable

    @property
    def unprotected(self) -> list[RequirementScore]:
        """Requirements with at least one surviving mutant, worst first."""
        bad = [s for s in self.scores if s.survived > 0]
        return sorted(
            bad,
            key=lambda s: (s.score if s.score is not None else 0.0, s.requirement.id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "repo": self.repo,
            "model": self.model,
            "test_command": self.test_command,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 3),
            "llm_used": self.llm_used,
            "degraded_reason": self.degraded_reason,
            "usage": self.usage,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "totals": {
                "requirements": len(self.scores),
                "generated": self.total_generated,
                "killed": self.total_killed,
                "survived": self.total_survived,
                "timed_out": self.total_timed_out,
                "errored": self.total_errored,
                "suite_score": self.suite_score,
            },
            "requirements": [s.to_dict() for s in self.scores],
        }
