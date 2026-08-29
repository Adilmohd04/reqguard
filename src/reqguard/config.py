"""ReqGuard configuration.

Scaffolding — owned by the Supervisor (crew role S), task S0.3.

Every field has a default; a repository with no ``reqguard.yaml`` works. The
schema mirrors docs/03-implementation-manual.md section 14.

Project-specific context (Doc 1 section 18) is NOT part of this file — it comes
from an optional ``REQGUARD.md`` at the repo root, appended to the investigation
*user turn*, never to the cached system prefix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from reqguard.errors import ConfigError

__all__ = ["Config", "load_config", "CONFIG_FILENAME"]

CONFIG_FILENAME = "reqguard.yaml"


class ProjectConfig(BaseModel):
    language: Literal["python"] = "python"


class RequirementsConfig(BaseModel):
    path: str = "requirements.md"


class TestingConfig(BaseModel):
    command: str = "pytest"
    timeout_seconds: float = 300.0


class AnalysisConfig(BaseModel):
    max_mutations: int = 20
    max_repair_attempts: int = 2
    max_diff_lines: int = 15


class SafetyConfig(BaseModel):
    modify_original: bool = False
    sandbox_strategy: Literal["copy", "worktree"] = "copy"


class AgentEffort(BaseModel):
    analyze: str = "medium"
    investigate: str = "high"
    plan: str = "xhigh"
    repair: str = "high"
    baseline: str = "xhigh"


class AgentConfig(BaseModel):
    model: str = "claude-opus-5"
    effort: AgentEffort = Field(default_factory=AgentEffort)


class ReportConfig(BaseModel):
    output_dir: str = "./reqguard-report"


class Config(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    requirements: RequirementsConfig = Field(default_factory=RequirementsConfig)
    testing: TestingConfig = Field(default_factory=TestingConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)

    source_paths: list[str] = Field(default_factory=lambda: ["src/"])
    exclude: list[str] = Field(default_factory=lambda: ["**/migrations/**"])

    # ── convenience accessors used across the codebase ────────────────────
    @property
    def requirements_path(self) -> str:
        return self.requirements.path

    @property
    def test_command(self) -> str:
        return self.testing.command

    @property
    def test_timeout_s(self) -> float:
        return self.testing.timeout_seconds

    @property
    def max_mutations(self) -> int:
        return self.analysis.max_mutations

    @property
    def max_repair_attempts(self) -> int:
        return self.analysis.max_repair_attempts

    @property
    def max_diff_lines(self) -> int:
        return self.analysis.max_diff_lines


def load_config(repo: Path | str) -> Config:
    """Load ``reqguard.yaml`` from ``repo`` if present, else return defaults.

    Raises :class:`ConfigError` when the file exists but is malformed or names an
    unsupported language.
    """
    root = Path(repo)
    path = root / CONFIG_FILENAME if root.is_dir() else root
    if not path.is_file():
        return Config()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping at the top level")
    try:
        cfg = Config.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"{path} is malformed: {exc}") from exc
    if cfg.project.language != "python":
        raise ConfigError(
            f"ReqGuard v1 supports Python/pytest projects only; got {cfg.project.language!r}"
        )
    return cfg
