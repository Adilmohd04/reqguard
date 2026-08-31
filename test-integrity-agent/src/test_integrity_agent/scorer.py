"""Turn mutant results into per-requirement and suite-level integrity scores.

The score is deliberately simple, because a score nobody can explain is a score
nobody acts on:

    integrity = killed / (killed + survived)

Timeouts and errors are excluded from the denominator rather than counted as
kills. A timeout means the suite noticed *something* -- often an induced
infinite loop -- but not that it asserted anything about the requirement, and an
error means the mutant never produced a signal at all. Counting either as a kill
would inflate the number in exactly the direction that makes the tool useless.
"""

from __future__ import annotations

from .logging_setup import get_logger
from .models import (
    MutantOutcome,
    MutantResult,
    Requirement,
    RequirementScore,
    ScanReport,
)

_log = get_logger("scorer")


def score_requirement(
    requirement: Requirement,
    results: list[MutantResult],
    generated: int | None = None,
    note: str = "",
) -> RequirementScore:
    """Aggregate the mutants belonging to one requirement."""
    score = RequirementScore(
        requirement=requirement,
        generated=generated if generated is not None else len(results),
        results=list(results),
        note=note,
    )
    for result in results:
        if result.outcome is MutantOutcome.KILLED:
            score.killed += 1
        elif result.outcome is MutantOutcome.SURVIVED:
            score.survived += 1
        elif result.outcome is MutantOutcome.TIMEOUT:
            score.timed_out += 1
        else:
            score.errored += 1

    if score.generated == 0 and not score.note:
        score.note = "no mutation could be generated for this requirement"
    elif score.evaluable == 0 and not score.note:
        score.note = "no mutant produced a usable pass/fail signal"
    return score


def verdict(score: RequirementScore) -> str:
    """A one-word verdict for the terminal and the HTML report."""
    if score.generated == 0 or score.evaluable == 0:
        return "INCONCLUSIVE"
    if score.survived == 0:
        return "PROTECTED"
    if score.killed == 0:
        return "UNPROTECTED"
    return "PARTIAL"


def suspicious_all_survived(report: ScanReport) -> bool:
    """True when every evaluable mutant survived -- usually a setup problem.

    A suite that catches literally nothing is possible, but it is far more often
    a sign that the tests are importing the installed package instead of the
    sandboxed copy, or that ``test_command`` does not actually exercise the code
    under ``source_paths``.
    """
    evaluable = report.total_killed + report.total_survived
    return evaluable >= 3 and report.total_killed == 0


def build_report(
    repo: str,
    model: str,
    scores: list[RequirementScore],
    started_at: str,
    finished_at: str,
    duration_s: float,
    test_command: str,
    llm_used: bool,
    degraded_reason: str = "",
    usage: dict[str, int] | None = None,
    estimated_cost_usd: float = 0.0,
) -> ScanReport:
    """Assemble the final :class:`ScanReport`."""
    report = ScanReport(
        repo=repo,
        model=model,
        started_at=started_at,
        finished_at=finished_at,
        duration_s=duration_s,
        scores=scores,
        llm_used=llm_used,
        degraded_reason=degraded_reason,
        usage=usage or {},
        estimated_cost_usd=estimated_cost_usd,
        test_command=test_command,
    )
    if suspicious_all_survived(report):
        _log.warning(
            "every mutant survived -- check that the tests import the code under "
            "source_paths and not an installed copy of it"
        )
    return report
