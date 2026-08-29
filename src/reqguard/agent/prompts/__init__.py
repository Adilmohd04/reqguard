"""System-prompt loader. Owned by Track B.

The five stage prompts are plain Markdown, read from disk once and cached. They
go into the cached `system` block byte for byte (see `agent/client.py`) — there
is NO interpolation. Anything volatile (the requirement text, tool output, a run
id) belongs in the user turn, after the cache breakpoint.

Reference: docs/03-implementation-manual.md section 5.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

__all__ = ["NAMES", "load", "all_prompts"]

_DIR = Path(__file__).parent

# The prompt for each agent stage. `analyze` and `plan` are structured calls;
# `investigate` and `repair` drive the Tool Runner; `baseline` is the control arm.
NAMES: tuple[str, ...] = ("analyze", "investigate", "plan", "repair", "baseline")


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Return the system prompt `name`, cached. Raises KeyError for an unknown
    name rather than reading an arbitrary file."""
    if name not in NAMES:
        raise KeyError(f"unknown prompt {name!r}; expected one of {NAMES}")
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"prompt {name!r} is empty")
    return text


def all_prompts() -> dict[str, str]:
    """Every stage prompt, keyed by name. Used to warm the cache at startup."""
    return {name: load(name) for name in NAMES}
