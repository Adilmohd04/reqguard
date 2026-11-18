"""Per-stage model and effort policy. Owned by Track B.

Reference: docs/03-implementation-manual.md sections 1 and 1.1.

The effort ladder is low | medium | high | xhigh | max. Every entry below is a
deliberate choice, not a default:

- analyze     structured extraction from prose -- medium is enough
- investigate multi-file correlation over tool calls -- high
- plan        the hardest reasoning in the system -- xhigh (see section 5.4)
- repair      constrained by an investigation already in context -- high
- baseline    DELIBERATELY the highest setting -- the control arm must not be
              handicapped or the headline number is meaningless (section 16.2)
"""

from __future__ import annotations

from typing import Literal

Stage = Literal["analyze", "investigate", "plan", "repair", "baseline"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# The one model, used by every stage and by the baseline (section 16.2).
# Changed 2026-08-30 (COLLAB.md): migrated Anthropic -> Gemini -> OpenRouter.
# The Gemini free-tier key hit 429 RESOURCE_EXHAUSTED (no billing), so the
# human directed a switch to OpenRouter's free models. OpenRouter is an
# OpenAI-compatible gateway; BASE_URL points the `openai` SDK at it.
#
# Changed again 2026-11-18: `gpt-5` (bare id) was free on OpenRouter in
# August but isn't any more -- confirmed live against
# https://openrouter.ai/api/v1/models, `openai/gpt-5` now has real pricing,
# and a real run hit a 402 ("requires more credits") on a $0-balance key.
# Moved to the fallback this same comment already documented and the
# original probe already verified for BOTH structured output and tool
# calling: `nvidia/nemotron-3-ultra-550b-a55b:free`, confirmed free in that
# same live models listing (1M context). Do NOT swap the model without
# re-probing both capabilities, or stages 2/5 (tools) or 1/3 (structured
# output) will break. Pinned to the exact id -- no `-latest` alias that
# could move under us mid-project.
MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
BASE_URL: str = "https://openrouter.ai/api/v1"

# Non-streaming parse/tool-runner calls: keep the response under the SDK HTTP
# timeout. 16k is the manual's figure (section 5.1).
MAX_TOKENS: int = 8000

STAGES: tuple[Stage, ...] = ("analyze", "investigate", "plan", "repair", "baseline")

EFFORT: dict[Stage, Effort] = {
    "analyze": "medium",
    "investigate": "high",
    "plan": "xhigh",
    "repair": "high",
    "baseline": "xhigh",
}

_VALID_EFFORT: frozenset[str] = frozenset(("low", "medium", "high", "xhigh", "max"))


def effort_for(stage: str) -> Effort:
    """Effort setting for a stage. Raises KeyError on an unknown stage rather
    than silently falling back to a default -- a mistyped stage name must fail
    loudly, not run at the wrong effort."""
    return EFFORT[stage]  # type: ignore[index]


def validate() -> None:
    """Assert the policy table is internally consistent. Called by the client
    smoke check so a bad edit here fails the B1 gate, not a runtime call."""
    missing = [s for s in STAGES if s not in EFFORT]
    if missing:
        raise AssertionError(f"policy: stages missing an effort entry: {missing}")
    extra = [s for s in EFFORT if s not in STAGES]
    if extra:
        raise AssertionError(f"policy: unknown stage in EFFORT: {extra}")
    bad = {s: e for s, e in EFFORT.items() if e not in _VALID_EFFORT}
    if bad:
        raise AssertionError(f"policy: invalid effort values: {bad}")
    if EFFORT["baseline"] != "xhigh":
        raise AssertionError(
            "policy: baseline effort must be 'xhigh' -- the control arm gets the "
            "highest setting (manual section 16.2)"
        )
    if MODEL != "nvidia/nemotron-3-ultra-550b-a55b:free":
        raise AssertionError(f"policy: unexpected model {MODEL!r}")
