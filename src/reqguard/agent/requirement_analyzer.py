"""Stage 1 — Requirement Analyzer. Owned by Track B.

Turns a requirements document into a list of testable `Requirement` contracts.
A single structured call (`messages.parse`), no tools. Reference:
docs/03-implementation-manual.md sections 5.2 and 10.2.

The orchestrator calls `run(requirements_path, trace)` once per analysis.
"""

from __future__ import annotations

from pathlib import Path

from reqguard.models import Requirement
from reqguard.agent import client, prompts
from reqguard.agent.client import Trace

__all__ = ["run"]

_STAGE = "analyze"


def _numbered(text: str) -> str:
    """1-indexed `Lnn: line` so the model can cite `source_location` as file:Lnn."""
    return "\n".join(
        f"L{i}: {line}" for i, line in enumerate(text.splitlines(), start=1)
    )


def _user_turn(filename: str, text: str) -> str:
    """The volatile half of the call — everything that changes per run. Goes in
    the user turn, never the cached system block."""
    return (
        f"Requirements document: {filename}\n"
        f"Cite source_location as `{filename}:Lnn` using these line numbers.\n\n"
        f"{_numbered(text)}"
    )


def run(requirements_path: str | Path, trace: Trace | None = None) -> list[Requirement]:
    """Extract every testable requirement from the document at `requirements_path`.

    Aspirational statements that no test could falsify are dropped by the model
    per the prompt; this function does not second-guess that.
    """
    path = Path(requirements_path)
    text = path.read_text(encoding="utf-8")
    user = _user_turn(path.name, text)
    result = client.structured(
        _STAGE,
        prompts.load(_STAGE),
        user,
        # OpenAI's structured output rejects a bare top-level array as
        # response_format -- `structured()` wraps/unwraps this transparently
        # via a one-field envelope model (client.py: `_envelope_for`), so
        # this call site stays `list[Requirement]` unchanged.
        list[Requirement],  # type: ignore[arg-type]
        trace,
    )
    return list(result)
