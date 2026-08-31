"""test-integrity-agent -- does your test suite actually protect your requirements?

Given a repository and a set of written requirements, this package determines
whether the existing test suite would catch violations of those requirements. It
generates targeted mutations that each break one specific requirement, runs the
suite against every mutant in an isolated copy of the repo, and reports which
requirements are genuinely protected and which only appear to be.

Library usage::

    from test_integrity_agent import Agent

    agent = Agent.from_path("/path/to/repo", mutants_per_req=3, workers=8)
    report = agent.scan()

    print(report.suite_score)                # 0.0 - 1.0, or None
    for score in report.unprotected:
        print(score.requirement.id, score.requirement.text)
        for survivor in score.survivors:
            print(survivor.diff)
"""

from __future__ import annotations

__version__ = "0.1.0"

from .agent import Agent
from .config import DEFAULT_MODEL, Config
from .errors import (
    BaselineFailureError,
    ConfigError,
    LLMError,
    MissingAPIKeyError,
    NoRequirementsError,
    RepositoryError,
    RequirementParseError,
    SuiteCommandError,
    TIAError,
)
from .models import (
    MutantOutcome,
    MutantResult,
    Mutation,
    MutationSource,
    Requirement,
    RequirementScore,
    ScanReport,
    SourceSpan,
)
from .report import render_html, to_json, write_html, write_json

__all__ = [
    "Agent",
    "BaselineFailureError",
    "Config",
    "ConfigError",
    "DEFAULT_MODEL",
    "LLMError",
    "MissingAPIKeyError",
    "MutantOutcome",
    "MutantResult",
    "Mutation",
    "MutationSource",
    "NoRequirementsError",
    "Requirement",
    "RequirementParseError",
    "RequirementScore",
    "RepositoryError",
    "ScanReport",
    "SourceSpan",
    "TIAError",
    "SuiteCommandError",
    "__version__",
    "render_html",
    "to_json",
    "write_html",
    "write_json",
]
