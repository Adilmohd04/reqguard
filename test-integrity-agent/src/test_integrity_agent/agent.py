"""The orchestrator: the library entry point and the engine behind ``tia scan``.

    from test_integrity_agent import Agent

    agent = Agent.from_path("/path/to/repo")
    report = agent.scan()
    print(report.suite_score)

The pipeline is: parse requirements -> index the repository -> locate the code
implementing each requirement -> generate requirement-violating mutations ->
verify the baseline suite is green -> run every mutant in an isolated sandbox ->
score.

Mutation generation and test execution are both concurrent. Generation is
network-bound and execution is process-bound, so they use separate pools and
``workers`` sizes them both.
"""

from __future__ import annotations

import datetime as _dt
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .cache import ResponseCache
from .config import Config, api_key_present
from .errors import LLMError, MissingAPIKeyError, RepositoryError, TIAError
from .llm import LLMClient
from .locator import Locator, Symbol, index_repository
from .logging_setup import get_logger
from .models import (
    MutantResult,
    Mutation,
    Requirement,
    RequirementScore,
    ScanReport,
    SourceSpan,
)
from .mutator import generate_mutations
from .requirements import load_requirements, select
from .runner import SandboxPool, run_mutant, verify_baseline
from .scorer import build_report, score_requirement

_log = get_logger("agent")

ProgressHook = Callable[[str, str, int, int], None]
"""``(phase, detail, done, total)`` -- called from worker threads, so keep it cheap."""


def _noop_progress(phase: str, detail: str, done: int, total: int) -> None:
    return None


@dataclass
class _Plan:
    """What was decided for one requirement before any test ran."""

    requirement: Requirement
    spans: list[SourceSpan]
    mutations: list[Mutation]
    note: str = ""


class Agent:
    """Runs a full integrity scan over one repository."""

    def __init__(
        self,
        config: Config,
        progress: ProgressHook | None = None,
    ) -> None:
        self.config = config
        self.progress: ProgressHook = progress or _noop_progress
        self.cache = ResponseCache(config.cache_path, enabled=True)
        self._llm: LLMClient | None = None
        self._llm_ready = False

    # ---- construction -------------------------------------------------

    @classmethod
    def from_path(
        cls,
        repo: str | Path,
        config_path: str | Path | None = None,
        progress: ProgressHook | None = None,
        **overrides: object,
    ) -> Agent:
        """Build an agent for ``repo``, loading ``tia.yaml`` and applying overrides.

        Overrides are the same names as the config fields, e.g.
        ``Agent.from_path(".", workers=8, model="claude-opus-5")``.
        """
        config = Config.load(
            Path(repo),
            Path(config_path) if config_path is not None else None,
        )
        for key, value in overrides.items():
            if value is None:
                continue
            if not hasattr(config, key):
                raise TIAError(f"Unknown configuration override: {key!r}")
            setattr(config, key, value)
        config.validate()
        return cls(config, progress=progress)

    # ---- lazily built collaborators -----------------------------------

    @property
    def llm(self) -> LLMClient | None:
        """The Anthropic client, or ``None`` in offline mode.

        Raises :class:`MissingAPIKeyError` when a key is required but absent --
        being silently downgraded to mechanical mutations is worse than an error
        that tells you exactly what to do.
        """
        if self._llm_ready:
            return self._llm
        self._llm_ready = True
        if self.config.offline:
            _log.info("offline mode: using AST mutation operators only")
            self._llm = None
            return None
        if not api_key_present():
            raise MissingAPIKeyError()
        self._llm = LLMClient(model=self.config.model, cache=self.cache)
        return self._llm

    # ---- pipeline stages ----------------------------------------------

    def load_requirements(self, only: Sequence[str] | None = None) -> list[Requirement]:
        """Parse every configured requirements source."""
        paths = self.config.resolved_requirement_paths()
        requirements = load_requirements(paths, self.config.repo, self.config.excluded)
        if only:
            requirements = select(requirements, list(only))
        _log.debug(
            "loaded requirements",
            extra={"context": {"count": len(requirements)}},
        )
        return requirements

    def index(self) -> list[Symbol]:
        """Index the repository's Python symbols."""
        return index_repository(
            self.config.repo,
            self.config.resolved_source_paths(),
            self.config.excluded,
        )

    def _read_source(self, relative_path: str) -> str:
        path = (self.config.repo / relative_path).resolve()
        root = self.config.repo.resolve()
        if root != path and root not in path.parents:
            raise RepositoryError(f"Refusing to read outside the repository: {relative_path}")
        return path.read_text(encoding="utf-8")

    def plan(self, requirements: list[Requirement], symbols: list[Symbol]) -> list[_Plan]:
        """Locate and mutate every requirement, concurrently."""
        locator = Locator(symbols, llm=self.llm, cache=self.cache)
        total = len(requirements)
        plans: list[_Plan] = []
        done = 0

        def build(requirement: Requirement) -> _Plan:
            spans = locator.locate(requirement)
            if not spans:
                return _Plan(
                    requirement=requirement,
                    spans=[],
                    mutations=[],
                    note="no source span could be matched to this requirement",
                )
            mutations, note = generate_mutations(
                requirement=requirement,
                spans=spans,
                read_file=self._read_source,
                count=self.config.mutants_per_req,
                llm=self.llm,
            )
            if not mutations and not note:
                note = "no valid mutation could be generated for the matched code"
            return _Plan(requirement, spans, mutations, note)

        workers = min(self.config.workers, max(1, total))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tia-plan") as pool:
            futures = {pool.submit(build, r): r for r in requirements}
            for future in as_completed(futures):
                requirement = futures[future]
                try:
                    plans.append(future.result())
                except (LLMError, RepositoryError) as exc:
                    _log.error(
                        "planning failed for a requirement",
                        extra={"context": {"requirement": requirement.id, "error": str(exc)}},
                    )
                    plans.append(_Plan(requirement, [], [], note=f"planning failed: {exc}"))
                done += 1
                self.progress("plan", requirement.id, done, total)

        order = {r.id: i for i, r in enumerate(requirements)}
        plans.sort(key=lambda p: order[p.requirement.id])
        return plans

    def execute(self, plans: list[_Plan]) -> list[RequirementScore]:
        """Run every planned mutant against the suite and score the results."""
        mutations = [m for plan in plans for m in plan.mutations]
        results_by_requirement: dict[str, list[MutantResult]] = {
            plan.requirement.id: [] for plan in plans
        }

        if not mutations:
            return [
                score_requirement(plan.requirement, [], generated=0, note=plan.note)
                for plan in plans
            ]

        workers = min(self.config.workers, len(mutations))
        with SandboxPool(self.config, size=workers) as pool:
            self.progress("baseline", self.config.test_command, 0, 1)
            verify_baseline(pool, self.config)
            self.progress("baseline", "green", 1, 1)

            total = len(mutations)
            done = 0
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="tia-run"
            ) as executor:
                futures = {
                    executor.submit(run_mutant, pool, self.config, mutation): mutation
                    for mutation in mutations
                }
                for future in as_completed(futures):
                    mutation = futures[future]
                    result = future.result()
                    results_by_requirement[mutation.requirement_id].append(result)
                    done += 1
                    self.progress("run", mutation.requirement_id, done, total)

        return [
            score_requirement(
                plan.requirement,
                results_by_requirement[plan.requirement.id],
                generated=len(plan.mutations),
                note=plan.note,
            )
            for plan in plans
        ]

    # ---- the whole thing ----------------------------------------------

    def scan(self, only: Sequence[str] | None = None) -> ScanReport:
        """Run the full pipeline and return the report."""
        started_monotonic = time.monotonic()
        started_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

        requirements = self.load_requirements(only)
        symbols = self.index()
        plans = self.plan(requirements, symbols)
        scores = self.execute(plans)

        duration = time.monotonic() - started_monotonic
        finished_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

        usage: dict[str, int] = {}
        cost = 0.0
        degraded = ""
        llm_used = self._llm is not None
        if self._llm is not None:
            usage = self._llm.usage.to_dict()
            price_in, price_out = self.config.pricing
            cost = self._llm.usage.cost_usd(price_in, price_out)
            degraded = self._llm.degraded_reason
        elif self.config.offline:
            degraded = "offline mode: AST operators only, mutations are not requirement-directed"

        self.cache.log_summary()

        return build_report(
            repo=str(self.config.repo),
            model=self.config.model if llm_used else "(none: AST operators)",
            scores=scores,
            started_at=started_at,
            finished_at=finished_at,
            duration_s=duration,
            test_command=self.config.test_command,
            llm_used=llm_used,
            degraded_reason=degraded,
            usage=usage,
            estimated_cost_usd=cost,
        )
