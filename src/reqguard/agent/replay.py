"""Offline replay of the agent stages. Owned by Track B.

ReqGuard's four LLM stages cost money and need a key. For CI, for
``reqguard doctor`` on a machine with no key, and for a deterministic demo, a
run can be **replayed** from recorded stage outputs instead.

Enable it by pointing ``REQGUARD_REPLAY`` at a directory of recorded JSON, or by
passing ``replay_dir=`` down from the CLI. Layout, per benchmark case:

    <replay_dir>/<case_name>/analyze.json          -> list[Requirement]
    <replay_dir>/<case_name>/<REQ>.investigate.json -> Investigation
    <replay_dir>/<case_name>/<REQ>.plan.json         -> MutationSpec
    <replay_dir>/<case_name>/<REQ>.repair.json       -> RepairProposal
    <replay_dir>/<case_name>/<REQ>.repair.<n>.json   -> RepairProposal (retry n, optional)
    <replay_dir>/<case_name>/<REQ>.baseline.json     -> BaselineFinding

A missing file raises :class:`FileNotFoundError` — replay never silently falls
back to a live call, because that would spend money the caller asked to avoid.
The recorded files are produced by a real run with ``--record <dir>`` (see the
orchestrator) or hand-authored for a fixture.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import TypeAdapter

from reqguard.models import Investigation, MutationSpec, Requirement, RepairProposal

__all__ = [
    "ENV_VAR",
    "resolve_dir",
    "enabled",
    "requirements",
    "investigation",
    "mutation_spec",
    "repair",
    "baseline_finding",
]

ENV_VAR = "REQGUARD_REPLAY"

_REQ_LIST = TypeAdapter(list[Requirement])


def resolve_dir(explicit: str | Path | None = None) -> Path | None:
    """The active replay directory: the explicit argument, else ``$REQGUARD_REPLAY``."""
    if explicit:
        return Path(explicit)
    env = os.environ.get(ENV_VAR)
    return Path(env) if env else None


def enabled(explicit: str | Path | None = None) -> bool:
    d = resolve_dir(explicit)
    return d is not None and d.is_dir()


def _case_dir(replay_dir: Path, case: str) -> Path:
    d = replay_dir / case
    if d.is_dir():
        return d
    # allow pointing REQGUARD_REPLAY straight at a single case dir
    if replay_dir.name == case or (replay_dir / "analyze.json").is_file():
        return replay_dir
    return d


def _load(path: Path, model: type):
    if not path.is_file():
        raise FileNotFoundError(
            f"replay file missing: {path} — record a run with `reqguard analyze "
            f"--record` or author the fixture"
        )
    return model.model_validate_json(path.read_text(encoding="utf-8"))


# ── per-stage loaders ────────────────────────────────────────────────────────

def requirements(replay_dir: Path, case: str) -> list[Requirement]:
    path = _case_dir(replay_dir, case) / "analyze.json"
    if not path.is_file():
        raise FileNotFoundError(f"replay file missing: {path}")
    return _REQ_LIST.validate_json(path.read_text(encoding="utf-8"))


def investigation(replay_dir: Path, case: str, req_id: str) -> Investigation:
    return _load(_case_dir(replay_dir, case) / f"{req_id}.investigate.json", Investigation)


def mutation_spec(replay_dir: Path, case: str, req_id: str) -> MutationSpec:
    return _load(_case_dir(replay_dir, case) / f"{req_id}.plan.json", MutationSpec)


def repair(replay_dir: Path, case: str, req_id: str, attempt: int = 0) -> RepairProposal:
    base = _case_dir(replay_dir, case)
    if attempt > 0:
        numbered = base / f"{req_id}.repair.{attempt}.json"
        if numbered.is_file():
            return _load(numbered, RepairProposal)
    return _load(base / f"{req_id}.repair.json", RepairProposal)


def baseline_finding(replay_dir: Path, case: str, req_id: str):
    from reqguard.evaluation.baseline import BaselineFinding

    return _load(_case_dir(replay_dir, case) / f"{req_id}.baseline.json", BaselineFinding)
