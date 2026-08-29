"""Deterministic evaluator for ReqGuard benchmark runs (task C5).

Owned by Track C (Evidence Layer). Doc 3 §§16.3, 17.

**This module contains NO LLM call of any kind and imports nothing from
``reqguard.agent``.** It scores *stored artifacts*, so re-scoring a run never
costs money and two people scoring one run get exactly one number. That
property is the whole reason the evidence layer is trustworthy — it is enforced
by the integration gate (which greps for the model SDK import) and by keeping
this file's imports to the frozen contract, the ground-truth loader, and the
trajectory reader.

Two metric *families* (Doc 3 §16.3), neither chosen after seeing the numbers:

* **Gap detection** — recall, precision, clean-case false-positive rate.
  Computed for BOTH arms because both answer the same yes/no question per
  requirement: *is this requirement adequately protected?*
* **Mutation kill rate** and **repair success rate** — ReqGuard ONLY, because
  the baseline structurally cannot generate mutations. Reporting them as a
  capability difference is the honest framing (§16.3), not smuggling them into
  a shared metric.

Also required by §17:

* the **inconclusive rate** is a first-class field, never buried;
* **cost** is summed from trajectory ``usage`` records, never estimated from a
  price table;
* the evaluator **re-asserts, from the recorded diffs, that no mutation touched
  a test file** (§6.5) — a scoring pass that cannot be fooled by its own input;
* a **SHA-256 of the ``benchmarks/`` tree** is recorded so a run whose cases
  changed after the fact is visible (§15.6 freeze rule).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from reqguard.tracing.trajectory import read_events

__all__ = [
    "is_test_file",
    "ConfusionMatrix",
    "GapDetectionMetrics",
    "MutationMetrics",
    "ArmMetrics",
    "gap_detection_metrics",
    "mutation_metrics",
    "sum_cost",
    "assert_no_mutation_touched_tests",
    "benchmarks_tree_sha256",
    "evaluate_arm",
]


# ── the test-file predicate (mirrors §6.5 / A3's not_a_test_file gate) ──────

def is_test_file(path: str) -> bool:
    """True if ``path`` is a pytest test file or lives under a tests/ dir.

    This MUST agree with the mutation engine's ``not_a_test_file`` gate
    (Doc 3 §6.5). The evaluator re-checks recorded diffs against it as an
    independent second opinion: if the engine were ever fooled, the evaluator
    refuses to score the run rather than quietly counting a test-file mutation
    as a kill. Over-rejecting here is the safe direction (AGENTS.md).
    """
    p = Path(path.replace("\\", "/"))
    name = p.name
    if name == "conftest.py":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    # any component named exactly "tests" (or "test") on the path
    return any(part in ("tests", "test") for part in p.parts)


# ── gap-detection scoring (both arms) ──────────────────────────────────────

@dataclass(frozen=True)
class ConfusionMatrix:
    """Per-requirement yes/no scoring, excluding inconclusive requirements.

    A requirement is a *ground-truth gap* when ``protected == false``. An arm
    *reports a gap* on it or not. Inconclusive requirements are counted
    separately and excluded from precision/recall so an environment failure is
    never scored as a correct or incorrect answer (§17, RunStatus rules echoed).
    """

    true_positive: int = 0   # gap, correctly reported
    false_positive: int = 0  # no gap, but a gap was reported (over-rejection)
    false_negative: int = 0  # gap, but not reported (missed)
    true_negative: int = 0   # no gap, none reported
    inconclusive: int = 0

    @property
    def scored(self) -> int:
        return (
            self.true_positive
            + self.false_positive
            + self.false_negative
            + self.true_negative
        )

    @property
    def total(self) -> int:
        return self.scored + self.inconclusive

    def __add__(self, other: ConfusionMatrix) -> ConfusionMatrix:
        return ConfusionMatrix(
            self.true_positive + other.true_positive,
            self.false_positive + other.false_positive,
            self.false_negative + other.false_negative,
            self.true_negative + other.true_negative,
            self.inconclusive + other.inconclusive,
        )


def _ratio(numerator: int, denominator: int) -> float | None:
    """Return numerator/denominator, or None when the denominator is 0.

    None means "undefined for this run" and is rendered as such — never as 0.0,
    which would falsely read as a measured value.
    """
    if denominator == 0:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class GapDetectionMetrics:
    recall: float | None
    precision: float | None
    clean_case_fp_rate: float | None
    inconclusive_rate: float | None
    confusion: ConfusionMatrix
    clean_case_requirements: int
    clean_case_false_positives: int


@dataclass(frozen=True)
class MutationMetrics:
    """ReqGuard-only. The baseline generates no mutations, so this is omitted
    for the baseline arm rather than reported as zero."""

    kill_rate: float | None
    repair_success_rate: float | None
    mutations_attempted: int
    mutations_killed: int
    repairs_attempted: int
    repairs_verified: int


@dataclass(frozen=True)
class ArmMetrics:
    arm: str
    gap_detection: GapDetectionMetrics
    mutation: MutationMetrics | None
    cost: dict[str, Any]
    runtime_seconds: float | None
    test_file_mutations: list[str] = field(default_factory=list)


# ── record shapes the evaluator scores (assembled from stored artifacts) ────
# These are plain protocols expressed as duck-typed reads: the evaluator takes
# mappings/objects with the named attributes so it can score `Verdict` models,
# loaded JSON dicts, or the dataclasses in benchmark.py identically.

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _requirement_is_gap(ground_truth_req: Any) -> bool:
    """A ground-truth gap is a requirement marked ``protected == false``."""
    return not bool(_get(ground_truth_req, "protected", True))


def gap_detection_metrics(
    scored_requirements: Iterable[Any],
) -> GapDetectionMetrics:
    """Score a flat sequence of per-requirement records.

    Each record must expose:
      - ``is_gap: bool``       — ground truth (protected == false)
      - ``reported_gap: bool`` — did the arm report a gap
      - ``inconclusive: bool`` — the arm produced no usable verdict
      - ``clean_case: bool``   — the requirement belongs to a clean case

    Clean-case false positives are ALSO counted in the confusion matrix as
    false positives (a clean-case requirement is never a gap), so precision
    reflects them; the clean-case FP rate is additionally reported on its own
    because §16.3 puts it in the headline table.
    """
    cm = ConfusionMatrix()
    clean_reqs = 0
    clean_fps = 0

    for rec in scored_requirements:
        is_gap = bool(_get(rec, "is_gap"))
        reported = bool(_get(rec, "reported_gap"))
        inconclusive = bool(_get(rec, "inconclusive"))
        clean_case = bool(_get(rec, "clean_case"))

        if clean_case:
            clean_reqs += 1

        if inconclusive:
            cm = cm + ConfusionMatrix(inconclusive=1)
            continue

        if is_gap and reported:
            cm = cm + ConfusionMatrix(true_positive=1)
        elif is_gap and not reported:
            cm = cm + ConfusionMatrix(false_negative=1)
        elif (not is_gap) and reported:
            cm = cm + ConfusionMatrix(false_positive=1)
            if clean_case:
                clean_fps += 1
        else:  # not is_gap and not reported
            cm = cm + ConfusionMatrix(true_negative=1)

    recall = _ratio(cm.true_positive, cm.true_positive + cm.false_negative)
    precision = _ratio(cm.true_positive, cm.true_positive + cm.false_positive)
    clean_fp_rate = _ratio(clean_fps, clean_reqs)
    inconclusive_rate = _ratio(cm.inconclusive, cm.total)

    return GapDetectionMetrics(
        recall=recall,
        precision=precision,
        clean_case_fp_rate=clean_fp_rate,
        inconclusive_rate=inconclusive_rate,
        confusion=cm,
        clean_case_requirements=clean_reqs,
        clean_case_false_positives=clean_fps,
    )


# ── mutation kill / repair (ReqGuard arm only) ──────────────────────────────

def mutation_metrics(mutations: Iterable[Any]) -> MutationMetrics:
    """Score mutation kill rate and repair success rate.

    Each mutation record must expose:
      - ``survived: bool | None`` — None is INCONCLUSIVE (env error): excluded
        from the denominator entirely, never scored as a kill (§17 / §33).
      - ``killed: bool``          — optional; defaults to ``survived is False``
      - ``repair_attempted: bool``
      - ``repair_verified: bool``

    A killed mutation is one the suite caught. Inconclusive mutations are
    dropped from both numerator and denominator so a missing dependency can
    never inflate the kill rate.
    """
    attempted = 0
    killed = 0
    repairs_attempted = 0
    repairs_verified = 0

    for m in mutations:
        survived = _get(m, "survived")
        if survived is None:
            # inconclusive — excluded from the kill-rate denominator
            pass
        else:
            attempted += 1
            explicit_killed = _get(m, "killed", None)
            was_killed = (
                bool(explicit_killed)
                if explicit_killed is not None
                else (survived is False)
            )
            if was_killed:
                killed += 1

        if bool(_get(m, "repair_attempted")):
            repairs_attempted += 1
            if bool(_get(m, "repair_verified")):
                repairs_verified += 1

    return MutationMetrics(
        kill_rate=_ratio(killed, attempted),
        repair_success_rate=_ratio(repairs_verified, repairs_attempted),
        mutations_attempted=attempted,
        mutations_killed=killed,
        repairs_attempted=repairs_attempted,
        repairs_verified=repairs_verified,
    )


# ── cost, summed from trajectory usage records ──────────────────────────────

_USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def sum_cost(trajectory_path: str | Path) -> dict[str, Any]:
    """Sum token usage from the ``api_call`` records of a trajectory file.

    §17: cost is summed from what was actually spent, never estimated from a
    price table. We report token totals and the api_call count; no per-token
    dollar figure is invented here.
    """
    totals: dict[str, int] = {f: 0 for f in _USAGE_TOKEN_FIELDS}
    api_calls = 0

    path = Path(trajectory_path)
    if not path.is_file():
        return {"api_calls": 0, **totals}

    for event in read_events(path):
        if _get(event, "type") != "api_call":
            continue
        api_calls += 1
        usage = _get(event, "usage") or {}
        for field_name in _USAGE_TOKEN_FIELDS:
            value = usage.get(field_name) if isinstance(usage, Mapping) else None
            try:
                totals[field_name] += int(value) if value is not None else 0
            except (TypeError, ValueError):
                continue

    return {"api_calls": api_calls, **totals}


# ── the scoring pass that cannot be fooled by its own input ─────────────────

def assert_no_mutation_touched_tests(mutations: Iterable[Any]) -> list[str]:
    """Re-assert from recorded diffs that no mutation targeted a test file.

    Returns the list of offending target paths (empty when clean). The caller
    (``evaluate_arm``) treats a non-empty result as a HARD failure of the run:
    the primary metric rests entirely on the engine never being able to edit a
    test (§6.5), so if a recorded diff violates it, the run's numbers are void.

    Both the declared ``target_file`` and any file path parsed from the unified
    diff header are checked — a diff cannot claim one target and patch another.
    """
    offenders: list[str] = []
    for m in mutations:
        target = _get(m, "target_file")
        if target and is_test_file(str(target)):
            offenders.append(str(target))
        diff = _get(m, "diff") or ""
        for touched in _paths_in_diff(str(diff)):
            if is_test_file(touched):
                offenders.append(touched)
    # de-dup while preserving order
    seen: set[str] = set()
    unique = []
    for o in offenders:
        if o not in seen:
            seen.add(o)
            unique.append(o)
    return unique


def _paths_in_diff(diff: str) -> list[str]:
    """Extract file paths from unified-diff ``---``/``+++`` headers."""
    paths: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            token = line[4:].strip().split("\t", 1)[0]
            if token in ("/dev/null", ""):
                continue
            # strip a/ b/ prefixes git adds
            if token[:2] in ("a/", "b/"):
                token = token[2:]
            paths.append(token)
    return paths


# ── benchmark tree hash (freeze rule, §15.6) ────────────────────────────────

def benchmarks_tree_sha256(benchmarks_dir: str | Path) -> str:
    """SHA-256 over the benchmarks/ tree: every file's relative path + bytes,
    in sorted order. Comparing two runs with different hashes proves a case was
    changed after results were seen (§15.6). Deterministic across machines:
    paths use forward slashes and are sorted before hashing.
    """
    root = Path(benchmarks_dir)
    hasher = hashlib.sha256()
    files = sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    for p in files:
        rel = p.relative_to(root).as_posix()
        # exclude generated results so re-scoring doesn't change the hash
        if rel.startswith("results/"):
            continue
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(p.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


# ── top-level per-arm evaluation ────────────────────────────────────────────

def evaluate_arm(
    arm: str,
    scored_requirements: Iterable[Any],
    *,
    mutations: Iterable[Any] | None = None,
    trajectory_path: str | Path | None = None,
    runtime_seconds: float | None = None,
    compute_mutation_metrics: bool = False,
) -> ArmMetrics:
    """Assemble every metric for one arm from its stored artifacts.

    ``compute_mutation_metrics`` gates the ReqGuard-only family: pass True for
    the ReqGuard arm, False (default) for the baseline, so the baseline never
    reports a mutation kill rate it structurally cannot have.

    Raises ``ValueError`` if any recorded mutation diff touched a test file —
    the run's numbers are void and must not be reported (§6.5).
    """
    muts = list(mutations or [])

    offenders = assert_no_mutation_touched_tests(muts)
    if offenders:
        raise ValueError(
            "recorded mutation diff targeted a test file — run is void "
            f"(§6.5): {offenders}"
        )

    gap = gap_detection_metrics(scored_requirements)
    mutation = mutation_metrics(muts) if compute_mutation_metrics else None
    cost = sum_cost(trajectory_path) if trajectory_path is not None else {
        "api_calls": 0,
        **{f: 0 for f in _USAGE_TOKEN_FIELDS},
    }

    return ArmMetrics(
        arm=arm,
        gap_detection=gap,
        mutation=mutation,
        cost=cost,
        runtime_seconds=runtime_seconds,
        test_file_mutations=offenders,  # always [] here (else we'd have raised)
    )
