"""The single Gemini call surface for ReqGuard. Owned by Track B.

Every model call in the repository goes through this module. Tracks A, C, D and E
import nothing from `reqguard.agent`, so the deterministic core and the evaluator
run in CI with no API key.

**Migration note (COLLAB.md session 6):** this module was Anthropic/Claude-opus-5
until 2026-08-30. The human explicitly directed a provider switch (no Anthropic
key available). Every shape below was verified empirically against the real
`google-genai` SDK this session -- not written from memory -- for exactly the
reason this repo's own docs warn about on the Anthropic side: a wrong API shape
costs a live run, not a review comment.

Verified against `google-genai==2.20.0` this session:

- `genai.Client()` — zero-arg, resolves `GEMINI_API_KEY` then `GOOGLE_API_KEY`
  from the environment. **Unlike the old Anthropic client, this validates the
  key at construction time, not on first call** — verified directly: a bare
  `genai.Client()` with no key set raises `ValueError` immediately. Anthropic's
  `anthropic.Anthropic()` deferred that to the first real request, which is
  what let `--replay` mode, `doctor`, and CI run with zero key present. To
  preserve that same "no key needed unless you actually call the model"
  contract, construction here is lazy (`_get_client()`, built and cached on
  first real use) rather than a module-level `_client = genai.Client()`.
- `client.models.generate_content(model=..., contents=..., config={...})` is
  the one call shape used for every stage, structured or tool-using.
- Structured output: `config["response_mime_type"] = "application/json"` +
  `config["response_schema"] = <BaseModel subclass or list[BaseModel]>` — the
  SDK validates and the parsed instance comes back on `response.parsed`.
  `list[Requirement]` (a bare generic, not wrapped) works directly — verified,
  matches what `requirement_analyzer.py` already passes as `output_format`.
- Tool use: pass **plain Python callables** in `config["tools"]`, not a
  JSON-schema dict and not an SDK decorator (there is no Gemini equivalent of
  `@beta_tool` — `tools/registry.py` was updated to return bare functions).
  The SDK inspects each callable's signature + docstring, executes it locally
  when the model calls it, and loops automatically — no manual runner, no
  `pause_turn` concept (that was Anthropic server-tool-specific; Gemini's
  automatic function calling either finishes with a real answer or the
  `finish_reason` check below catches it).
- Tools and `response_schema` **can be combined in one call** — verified: the
  model tool-calls, the SDK executes and re-prompts automatically, and the
  final turn's `response.parsed` is still populated. This is what makes
  `run_with_tools` a single call here rather than a driven loop.
- Effort ladder: Gemini's `thinking_config.thinking_level` only has four
  rungs (`MINIMAL`/`LOW`/`MEDIUM`/`HIGH`) against Anthropic's five
  (`low/medium/high/xhigh/max`). `xhigh` and `max` both collapse to `HIGH` --
  documented in `_THINKING_LEVEL` below, not silently lossy.
- System prompt: `config["system_instruction"]` — a plain string. Gemini's
  prompt caching is **implicit and server-managed** (no `cache_control`
  block to set), so nothing here opts into caching the way the old
  `_system_block`'s `cache_control: ephemeral` did — there is no equivalent
  knob on this SDK version. Track C's §11.3 cache-miss alarm was built
  against Anthropic's explicit `cache_read_input_tokens` field; it will not
  fire meaningfully against Gemini's `cached_content_token_count` semantics
  without Track C updating that check — flagged in COLLAB.md, not silently
  patched here since `tracing/trajectory.py` isn't this module's file.
- Usage/cost fields on `response.usage_metadata` (real field names, this SDK
  version): `prompt_token_count`, `candidates_token_count`,
  `cached_content_token_count`, `thoughts_token_count`, `total_token_count`.
  Different names than Anthropic's `input_tokens`/`output_tokens`/
  `cache_read_input_tokens`/`cache_creation_input_tokens` — `trajectory.py`'s
  `_coerce_usage` already duck-types via `model_dump()`, so it captures these
  new fields verbatim without needing a code change there; only a downstream
  reader hardcoded to the old field names would need updating (flagged, not
  found in this module's scope).
- Retries: the SDK retries internally (`tenacity`-backed, transient 5xx/429
  handled). No extra retry wrapper here.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Protocol, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from reqguard.errors import ReqGuardInternalError
from reqguard.agent import policy
from reqguard.agent.policy import MAX_TOKENS, MODEL

__all__ = ["structured", "run_with_tools", "Trace"]

_T = TypeVar("_T", bound=BaseModel)

# Lazy, cached client. `genai.Client()` validates the key at construction
# (verified -- unlike anthropic.Anthropic(), it raises immediately with no
# key set), so building it eagerly at import time would force every
# --replay/doctor/CI invocation to have a real key just to import this
# module. Never a hardcoded key; resolves GEMINI_API_KEY -> GOOGLE_API_KEY.
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()
    return _client

# Gemini's thinking ladder is 4 rungs; ours (policy.py) is 5. xhigh/max both
# collapse to HIGH -- there is no 5th tier to map them to on this SDK.
_THINKING_LEVEL: dict[str, str] = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "xhigh": "HIGH",
    "max": "HIGH",
}


class Trace(Protocol):
    """The slice of Track C's trajectory logger this module needs. The real
    object is injected by the orchestrator; this module never imports Track C."""

    def api_call(self, stage: str, response: Any) -> None: ...


class _NullTrace:
    """Used by the smoke check and by callers that have no trajectory yet."""

    def api_call(self, stage: str, response: Any) -> None:  # noqa: D401
        return None


def _config(
    stage: str,
    system: str,
    *,
    response_schema: Any | None = None,
    tools: list[Any] | None = None,
) -> dict[str, Any]:
    """Build the config dict common to every ReqGuard model call.

    Pure and side-effect free so the smoke check can assert the invariants
    without a network call.
    """
    cfg: dict[str, Any] = {
        "system_instruction": system,
        "thinking_config": {"thinking_level": _THINKING_LEVEL[policy.effort_for(stage)]},
        "max_output_tokens": MAX_TOKENS,
    }
    if response_schema is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = response_schema
    if tools is not None:
        cfg["tools"] = tools
    return cfg


def _assert_finished(response: Any) -> None:
    """No partial answers accepted. `finish_reason` anything but STOP --
    MAX_TOKENS, SAFETY, RECITATION, a dangling tool call the SDK gave up
    on -- is a loud failure, not a silently truncated result."""
    candidates = getattr(response, "candidates", None) or []
    for c in candidates:
        reason = getattr(c, "finish_reason", None)
        if reason is not None and reason != types.FinishReason.STOP:
            raise ReqGuardInternalError(
                f"unexpected finish_reason={reason!r} -- expected STOP"
            )


def structured(
    stage: str,
    system: str,
    user: str,
    output_format: type[_T],
    trace: Trace | None = None,
) -> _T:
    """A single structured call. Stages 1 (analyze) and 3 (plan) use this.

    Returns the validated model instance from `response.parsed`. The full
    response (for its `usage_metadata`) is handed to `trace`.
    """
    trace = trace or _NullTrace()
    response = _get_client().models.generate_content(
        model=MODEL,
        contents=user,
        config=_config(stage, system, response_schema=output_format),
    )
    trace.api_call(stage, response)
    _assert_finished(response)
    if response.parsed is None:
        raise ReqGuardInternalError(f"{stage}: model returned no parsed structured output")
    return response.parsed


def run_with_tools(
    stage: str,
    system: str,
    user: str,
    tools: list[Any],
    output_format: type[_T],
    trace: Trace | None = None,
) -> _T:
    """A tool-using stage ending in a validated structured output. Stages 2
    (investigate) and 5 (repair) use this.

    Gemini's automatic function calling handles the whole loop inside this one
    `generate_content` call: it calls each declared Python callable locally as
    the model requests, feeds results back, and keeps going until the model
    produces a final answer matching `output_format` -- verified this session,
    not assumed. `tools` must already be in the fixed, sorted order
    `tools/registry.py` returns (kept for parity with the old cache-ordering
    discipline, though Gemini's implicit caching doesn't depend on it the way
    Anthropic's explicit `cache_control` prefix did).
    """
    trace = trace or _NullTrace()
    response = _get_client().models.generate_content(
        model=MODEL,
        contents=user,
        config=_config(stage, system, response_schema=output_format, tools=tools),
    )
    trace.api_call(stage, response)
    _assert_finished(response)
    if response.parsed is None:
        raise ReqGuardInternalError(
            f"{stage}: tool-calling run produced no structured output "
            f"(finish_reason={getattr(response.candidates[0], 'finish_reason', None)!r})"
        )
    return response.parsed


# ── smoke check: the B1 gate ─────────────────────────────────────────────────

def _smoke() -> int:
    """`python -m reqguard.agent.client --smoke`. Asserts the call shape and the
    policy table without spending a token."""
    policy.validate()

    for stage in policy.STAGES:
        cfg = _config(stage, "SYSTEM PROMPT TEXT", response_schema=None)

        assert cfg["system_instruction"] == "SYSTEM PROMPT TEXT"
        assert "thinking_config" in cfg
        level = cfg["thinking_config"]["thinking_level"]
        assert level in ("LOW", "MEDIUM", "HIGH"), level
        assert level == _THINKING_LEVEL[policy.effort_for(stage)]
        assert "response_mime_type" not in cfg, "no schema passed -- must not appear"
        assert "tools" not in cfg, "no tools passed -- must not appear"

    with_schema = _config("analyze", "SYS", response_schema=str)
    assert with_schema["response_mime_type"] == "application/json"
    assert with_schema["response_schema"] is str

    with_tools = _config("investigate", "SYS", response_schema=str, tools=["placeholder"])
    assert with_tools["tools"] == ["placeholder"]

    # the one call surface exists -- checked on the class, not an instance,
    # since the smoke check must never require a key (no _get_client() call)
    assert callable(genai.Client.models.fget), "genai.Client has no .models property"

    # finish_reason assertion fires on anything but STOP
    class _Candidate:
        finish_reason = types.FinishReason.MAX_TOKENS

    class _Truncated:
        candidates = [_Candidate()]

    try:
        _assert_finished(_Truncated())
    except ReqGuardInternalError:
        pass
    else:
        raise AssertionError("finish_reason assertion did not fire")

    print("client smoke OK:", MODEL, "| thinking levels", dict(_THINKING_LEVEL))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reqguard.agent.client")
    parser.add_argument("--smoke", action="store_true", help="assert call shape + policy")
    args = parser.parse_args(argv)
    if args.smoke:
        return _smoke()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
