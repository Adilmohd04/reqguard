"""Mutation result helpers.

Reference: docs/03-implementation-manual.md sections 8, 9.

This module provides utilities for working with MutationResult objects.
The core data model is defined in reqguard.models.MutationResult.
"""

from __future__ import annotations

from reqguard.models import MutationResult, RunStatus, TestOutcome


def is_survived(result: MutationResult) -> bool | None:
    """Check if a mutation survived (tests passed on mutated code).

    Returns:
        True if survived (mutated exit_code == 0)
        False if killed (mutated exit_code == 1)
        None if inconclusive (ENVIRONMENT_ERROR or TIMEOUT)
    """
    if result.mutated.status is not RunStatus.OK:
        return None
    return result.mutated.exit_code == 0


def is_killed(result: MutationResult) -> bool | None:
    """Check if a mutation was killed (tests failed on mutated code).

    Returns:
        True if killed (mutated exit_code == 1)
        False if survived (mutated exit_code == 0)
        None if inconclusive
    """
    if result.mutated.status is not RunStatus.OK:
        return None
    return result.mutated.exit_code == 1


def is_inconclusive(result: MutationResult) -> bool:
    """Check if the mutation result is inconclusive."""
    return result.mutated.status is not RunStatus.OK


def baseline_is_green(result: MutationResult) -> bool:
    """Check if the baseline (unmutated) run was green."""
    return result.baseline.status is RunStatus.OK and result.baseline.exit_code == 0


def summarize(result: MutationResult) -> str:
    """Create a human-readable summary of the mutation result."""
    if result.survived is True:
        return f"SURVIVED: {result.mutation_id} (tests passed on mutant)"
    elif result.survived is False:
        return f"KILLED: {result.mutation_id} (tests failed on mutant)"
    else:
        return f"INCONCLUSIVE: {result.mutation_id} ({result.mutated.status.value}: {result.mutated.reason or 'unknown'})"


__all__ = [
    "is_survived",
    "is_killed",
    "is_inconclusive",
    "baseline_is_green",
    "summarize",
    "MutationResult",
    "TestOutcome",
    "RunStatus",
]