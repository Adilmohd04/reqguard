"""Configuration: the ``tia.yaml`` schema, defaults, and validation.

Precedence, highest first: explicit CLI flags, then ``tia.yaml``, then the
defaults in this module. The API key is deliberately *not* part of this schema
-- it is read from the environment only, so a config file committed to a repo
can never leak one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError, split_command

DEFAULT_MODEL = "claude-sonnet-4-6"
"""Model used unless overridden. Any current Claude model id works, e.g.
``claude-opus-5`` for harder codebases or ``claude-haiku-4-5`` for cheap runs."""

DEFAULT_CONFIG_NAME = "tia.yaml"

CONFIG_FILENAMES = ("tia.yaml", "tia.yml", ".tia.yaml")

# Input/output USD per million tokens, used only for the cost estimate we print.
# Unknown models fall back to the Sonnet row and the estimate is marked approximate.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}

DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "build",
    "dist",
    ".eggs",
    "htmlcov",
    ".tia_cache",
    ".idea",
    ".vscode",
)

STARTER_CONFIG = """\
# tia.yaml -- test-integrity-agent configuration.
# Every key below is optional; the values shown are the defaults.
# The Anthropic API key is NEVER read from this file. Set ANTHROPIC_API_KEY
# in your environment instead.

# Where the requirements live. Markdown files, a YAML file of requirements,
# or Python modules whose docstrings carry "Requirement:" lines.
requirements:
  - REQUIREMENTS.md

# Source roots that implement those requirements. Mutations are only ever
# applied inside these paths.
source_paths:
  - src

# How to run the test suite. Run from the repo root of an isolated copy.
test_command: pytest -x -q

# Per-mutant wall-clock limit, in seconds.
timeout: 120

# Candidate mutations generated per requirement.
mutants_per_req: 3

# Parallel workers for test execution (and for mutation generation).
workers: 4

# Claude model used to locate requirements in code and to write mutations.
model: claude-sonnet-4-6

# Directory for the on-disk LLM response cache, relative to the repo root.
cache_dir: .tia_cache

# Fail the process (exit code 1) when any requirement has a surviving mutant.
fail_on_survivor: true

# Extra directory names never copied into the sandbox or scanned for source.
exclude_dirs: []
"""


def _as_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ConfigError(f"`{key}` must be a string or a list of strings, got {type(value).__name__}.")


@dataclass
class Config:
    """Resolved configuration for one scan."""

    repo: Path = field(default_factory=Path.cwd)
    requirements: list[str] = field(default_factory=lambda: ["REQUIREMENTS.md"])
    source_paths: list[str] = field(default_factory=lambda: ["src"])
    test_command: str = "pytest -x -q"
    timeout: int = 120
    mutants_per_req: int = 3
    workers: int = 4
    model: str = DEFAULT_MODEL
    cache_dir: str = ".tia_cache"
    fail_on_survivor: bool = True
    exclude_dirs: list[str] = field(default_factory=list)
    offline: bool = False
    """Skip the LLM entirely and use AST operators. Set by ``--offline``."""
    verbose: bool = False
    config_path: Path | None = None

    # ---- construction -------------------------------------------------

    @classmethod
    def load(cls, repo: Path, config_path: Path | None = None) -> Config:
        """Load configuration for ``repo``, from ``config_path`` if given.

        A missing config file is not an error -- the defaults are usable -- but
        an explicitly requested file that does not exist is.
        """
        repo = repo.expanduser().resolve()
        if not repo.is_dir():
            raise ConfigError(f"Repository path does not exist or is not a directory: {repo}")

        path: Path | None = None
        if config_path is not None:
            path = config_path.expanduser()
            if not path.is_absolute():
                path = (repo / path).resolve()
            if not path.is_file():
                raise ConfigError(
                    f"Config file not found: {path}",
                    hint="Run `tia init` to write a starter tia.yaml.",
                )
        else:
            for name in CONFIG_FILENAMES:
                candidate = repo / name
                if candidate.is_file():
                    path = candidate
                    break

        data: dict[str, Any] = {}
        if path is not None:
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
            except OSError as exc:
                raise ConfigError(f"Could not read {path}: {exc}") from exc
            if raw is None:
                raw = {}
            if not isinstance(raw, dict):
                raise ConfigError(f"{path} must contain a YAML mapping at the top level.")
            data = raw

        known = {f.name for f in fields(cls)} - {"repo", "config_path", "offline", "verbose"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ConfigError(
                f"Unknown option(s) in {path}: {', '.join(unknown)}",
                hint=f"Valid options are: {', '.join(sorted(known))}",
            )

        cfg = cls(repo=repo, config_path=path)
        if "requirements" in data:
            cfg.requirements = _as_list(data["requirements"], "requirements")
        if "source_paths" in data:
            cfg.source_paths = _as_list(data["source_paths"], "source_paths")
        if "exclude_dirs" in data:
            cfg.exclude_dirs = _as_list(data["exclude_dirs"], "exclude_dirs")
        for key in ("test_command", "model", "cache_dir"):
            if key in data:
                value = data[key]
                if not isinstance(value, str) or not value.strip():
                    raise ConfigError(f"`{key}` must be a non-empty string.")
                setattr(cfg, key, value.strip())
        for key in ("timeout", "mutants_per_req", "workers"):
            if key in data:
                value = data[key]
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ConfigError(f"`{key}` must be an integer, got {value!r}.")
                setattr(cfg, key, value)
        if "fail_on_survivor" in data:
            value = data["fail_on_survivor"]
            if not isinstance(value, bool):
                raise ConfigError("`fail_on_survivor` must be true or false.")
            cfg.fail_on_survivor = value
        cfg.validate()
        return cfg

    # ---- validation ---------------------------------------------------

    def validate(self) -> None:
        """Check every value is usable. Raises :class:`ConfigError` on the first problem."""
        if self.timeout < 1:
            raise ConfigError(f"`timeout` must be at least 1 second, got {self.timeout}.")
        if self.timeout > 3600:
            raise ConfigError(f"`timeout` must be at most 3600 seconds, got {self.timeout}.")
        if not 1 <= self.mutants_per_req <= 25:
            raise ConfigError(
                f"`mutants_per_req` must be between 1 and 25, got {self.mutants_per_req}."
            )
        if not 1 <= self.workers <= 64:
            raise ConfigError(f"`workers` must be between 1 and 64, got {self.workers}.")
        if not self.test_command.strip():
            raise ConfigError("`test_command` must not be empty.")
        try:
            if not split_command(self.test_command):
                raise ConfigError("`test_command` must not be empty.")
        except ValueError as exc:
            raise ConfigError(f"`test_command` is not a parseable shell command: {exc}") from exc
        if not self.requirements:
            raise ConfigError(
                "`requirements` is empty -- there is nothing to check.",
                hint="Point it at a markdown file, a YAML file, or a Python package.",
            )
        if not self.source_paths:
            raise ConfigError(
                "`source_paths` is empty -- there is nothing to mutate.",
                hint='Set it to the directory holding your code, e.g. ["src"].',
            )
        if not self.model.strip():
            raise ConfigError("`model` must not be empty.")

    # ---- derived values -----------------------------------------------

    @property
    def excluded(self) -> frozenset[str]:
        return frozenset(DEFAULT_EXCLUDE_DIRS) | frozenset(self.exclude_dirs)

    @property
    def cache_path(self) -> Path:
        path = Path(self.cache_dir)
        return path if path.is_absolute() else self.repo / path

    @property
    def pricing(self) -> tuple[float, float]:
        return MODEL_PRICING.get(self.model, MODEL_PRICING[DEFAULT_MODEL])

    @property
    def pricing_is_exact(self) -> bool:
        return self.model in MODEL_PRICING

    def resolved_requirement_paths(self) -> list[Path]:
        """Expand ``requirements`` entries (which may be globs) to existing paths."""
        found: list[Path] = []
        for entry in self.requirements:
            candidate = self.repo / entry
            if candidate.exists():
                found.append(candidate)
                continue
            matches = sorted(self.repo.glob(entry))
            if matches:
                found.extend(matches)
            else:
                raise ConfigError(
                    f"Requirements source not found: {entry} (looked in {self.repo})",
                    hint="Paths in `requirements` are relative to the repository root.",
                )
        return found

    def resolved_source_paths(self) -> list[Path]:
        """Expand ``source_paths`` to existing paths inside the repo."""
        found: list[Path] = []
        for entry in self.source_paths:
            candidate = self.repo / entry
            if candidate.exists():
                found.append(candidate)
                continue
            matches = sorted(self.repo.glob(entry))
            if matches:
                found.extend(matches)
            else:
                raise ConfigError(
                    f"Source path not found: {entry} (looked in {self.repo})",
                    hint="Paths in `source_paths` are relative to the repository root.",
                )
        return found

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": str(self.repo),
            "requirements": self.requirements,
            "source_paths": self.source_paths,
            "test_command": self.test_command,
            "timeout": self.timeout,
            "mutants_per_req": self.mutants_per_req,
            "workers": self.workers,
            "model": self.model,
            "cache_dir": self.cache_dir,
            "fail_on_survivor": self.fail_on_survivor,
            "exclude_dirs": self.exclude_dirs,
            "offline": self.offline,
        }


def write_starter_config(repo: Path, force: bool = False) -> Path:
    """Write ``tia.yaml`` into ``repo``. Returns the path written."""
    target = repo / DEFAULT_CONFIG_NAME
    if target.exists() and not force:
        raise ConfigError(
            f"{target} already exists.",
            hint="Pass --force to overwrite it.",
        )
    target.write_text(STARTER_CONFIG, encoding="utf-8")
    return target


def api_key_present() -> bool:
    """True when an Anthropic API key is available in the environment.

    The value is never returned, logged, or stored -- only its presence.
    """
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
