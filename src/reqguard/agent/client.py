"""The single OpenRouter call surface for ReqGuard. Owned by Track B.

Every model call in the repository goes through this module. Tracks A, C, D and E
import nothing from `reqguard.agent`, so the deterministic core and the evaluator
run in CI with no API key.

**Migration note (COLLAB.md):** Anthropic/claude-opus-5 -> Gemini -> now
OpenRouter, each an explicit human decision (no Anthropic key; Gemini free tier
hit 429 RESOURCE_EXHAUSTED with no billing). OpenRouter fronts many models behind
an **OpenAI-compatible** API, so this module uses the `openai` SDK pointed at
`https://openrouter.ai/api/v1`. The external contract is unchanged:
`structured()` and `run_with_tools()` keep their exact signatures, so no stage
module or the orchestrator changed.

Verified empirically against `openai==3.6.0` + OpenRouter this session (a probe
script ran real structured-output and tool-calling calls before this was
written -- not from memory):

- `OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)`.
  Constructed **lazily** (`_get_client()`), so --replay/doctor/CI need no key --
  same contract the Gemini port preserved. Key resolves from `OPENROUTER_API_KEY`.
- Model: pinned in policy.py. A free model must support BOTH structured output
  and tool calling; `nvidia/nemotron-3-super-120b-a12b:free` was verified to do
  both. Many free models do neither -- do not swap the model without re-probing.
- Structured output: `client.beta.chat.completions.parse(..., response_format=
  <BaseModel or list-wrapper>)`; the parsed instance is on
  `choices[0].message.parsed`. Top-level `list[Requirement]` is not a valid
  OpenAI `response_format` (must be a single BaseModel), so `structured()` wraps
  a bare `list[...]` in a one-field envelope model and unwraps it -- transparent
  to callers, `requirement_analyzer.py` still passes `list[Requirement]`.
- Tool use: OpenAI-style `tools=[{"type":"function","function":{...}}]` from
  `tools/registry.py`. Unlike Gemini's automatic function calling, OpenAI does
  NOT execute tools for you -- `run_with_tools` drives the loop: call, run the
  requested functions locally, feed results back as `role:"tool"` messages,
  repeat until the model stops requesting tools, then one final structured parse.
- Effort ladder: OpenAI/OpenRouter has no first-class "effort" knob across all
  models. Mapped to `reasoning_effort` where the model honors it (low/medium/
  high; our xhigh/max collapse to high) and otherwise ignored server-side --
  documented in `_REASONING_EFFORT`, not silently lossy.
- Usage: `response.usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`).
  `trajectory.py`'s `_coerce_usage` duck-types via `model_dump()`, so it captures
  these verbatim; only a reader hardcoded to Anthropic/Gemini field names would
  need updating (flagged in COLLAB.md, not in this module's scope).
- Retries: the `openai` SDK retries transient 429/5xx internally. No extra wrapper.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Protocol, TypeVar, get_args, get_origin

import openai
from openai import OpenAI
from pydantic import BaseModel, create_model

from reqguard.errors import ReqGuardInternalError
from reqguard.agent import policy
from reqguard.agent.policy import BASE_URL, MAX_TOKENS, MODEL

__all__ = ["structured", "run_with_tools", "Trace"]

_T = TypeVar("_T", bound=BaseModel)

# OpenRouter tool-call round-trips before we stop looping and FORCE a final
# structured answer (tools withheld on that last call). Small models on the free
# tier tend to keep calling tools; capping low keeps a single case cheap.
_MAX_TOOL_ROUNDS = 6

# Lazy, cached client -- built on first real use so --replay/doctor/CI need no
# key. Never a hardcoded key; resolves OPENROUTER_API_KEY from the environment
# (loaded from .env by the CLI on startup).
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        import os

        # Provider resolution, in priority order. Both are OpenAI-compatible;
        # only the base_url + key differ. Conduit first (its own endpoint),
        # then OpenRouter. BASE_URL in policy.py is the default fallback.
        conduit_key = os.environ.get("CONDUIT_API_KEY")
        if conduit_key:
            base = os.environ.get("CONDUIT_BASE_URL") or BASE_URL
            _client = OpenAI(base_url=base, api_key=conduit_key)
            return _client

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ReqGuardInternalError(
                "no provider key set (CONDUIT_API_KEY or OPENROUTER_API_KEY) -- "
                "a live run needs one (--replay needs none)."
            )
        _client = OpenAI(base_url=BASE_URL, api_key=key)
    return _client


# OpenAI reasoning_effort has low|medium|high; our ladder (policy.py) has five.
# xhigh/max collapse to high -- there is no higher rung on this API.
_REASONING_EFFORT: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}


class Trace(Protocol):
    """The slice of Track C's trajectory logger this module needs. The real
    object is injected by the orchestrator; this module never imports Track C."""

    def api_call(self, stage: str, response: Any) -> None: ...


class _NullTrace:
    """Used by the smoke check and by callers that have no trajectory yet."""

    def api_call(self, stage: str, response: Any) -> None:  # noqa: D401
        return None


# ── list[...] response_format shim ──────────────────────────────────────────
# OpenAI structured output requires a single BaseModel as response_format; a
# bare top-level `list[Requirement]` (what requirement_analyzer passes) is not
# valid. Wrap it in a one-field envelope and unwrap transparently.

def _is_list_type(tp: Any) -> bool:
    return get_origin(tp) in (list, list.__class__) or get_origin(tp) is list


def _envelope_for(list_type: Any) -> tuple[type[BaseModel], Any]:
    (item_type,) = get_args(list_type)
    envelope = create_model(
        f"_ItemsEnvelope_{getattr(item_type, '__name__', 'X')}",
        items=(list_type, ...),
    )
    return envelope, item_type


def _messages(system: str, user: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _common_kwargs(stage: str) -> dict[str, Any]:
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": _REASONING_EFFORT[policy.effort_for(stage)],
    }


def _assert_finished(response: Any) -> None:
    """No partial answers. Any finish_reason but 'stop' or 'tool_calls'
    (length/content_filter/etc.) is a loud failure, never a silent truncation."""
    for choice in getattr(response, "choices", None) or []:
        reason = getattr(choice, "finish_reason", None)
        if reason not in (None, "stop", "tool_calls"):
            raise ReqGuardInternalError(
                f"unexpected finish_reason={reason!r} -- expected stop"
            )


def _call(fn: Any, **kwargs: Any) -> Any:
    """Call an OpenAI SDK method, translating any HTTP-status failure
    (`openai.APIStatusError` -- 401/403/404/429/5xx, covers auth failures,
    account-level gates, rate limits, and provider outages alike) into a
    `ReqGuardInternalError` carrying the provider's own message.

    Found by actually running a live call, not written defensively in
    advance: a Conduit 403 ("subscription_required" -- the free tier gates on
    a Telegram channel join, an account-policy condition, not a code bug)
    came back as a raw, unhandled framework traceback all the way to the
    terminal. `_assert_finished` and the missing-key check already raise
    typed errors for the failure modes this module causes itself; this is
    the same discipline applied to failures the *provider* returns instead.
    """
    try:
        return fn(**kwargs)
    except openai.APIStatusError as exc:
        raise ReqGuardInternalError(
            f"provider error ({exc.status_code}): {exc.message}"
        ) from exc


def structured(
    stage: str,
    system: str,
    user: str,
    output_format: type[_T],
    trace: Trace | None = None,
) -> _T:
    """A single structured call. Stages 1 (analyze) and 3 (plan) use this.

    Returns the validated model instance. A bare `list[...]` output_format is
    wrapped/unwrapped transparently (OpenAI needs a single BaseModel).
    """
    trace = trace or _NullTrace()

    wrap = _is_list_type(output_format)
    response_format: Any = output_format
    if wrap:
        response_format, _ = _envelope_for(output_format)

    response = _call(
        _get_client().beta.chat.completions.parse,
        messages=_messages(system, user),
        response_format=response_format,
        **_common_kwargs(stage),
    )
    trace.api_call(stage, response)
    _assert_finished(response)

    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise ReqGuardInternalError(f"{stage}: model returned no parsed structured output")
    return parsed.items if wrap else parsed  # type: ignore[return-value,attr-defined]


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

    OpenAI does not auto-execute tools, so this drives the loop: send the tool
    schemas, run any requested functions locally, feed results back, repeat
    until the model stops requesting tools, then a final structured parse.
    `tools` is the list from `tools/registry.py`: OpenAI function schema dicts,
    each carrying a bound `_impl` callable under a side table (see registry).
    """
    trace = trace or _NullTrace()
    client = _get_client()

    schemas = [t["schema"] for t in tools]
    impls = {t["schema"]["function"]["name"]: t["impl"] for t in tools}

    messages = _messages(system, user)

    for _round in range(_MAX_TOOL_ROUNDS):
        response = _call(
            client.chat.completions.create,
            messages=messages,
            tools=schemas,
            **_common_kwargs(stage),
        )
        trace.api_call(stage, response)
        _assert_finished(response)

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in tool_calls],
            }
        )
        for tc in tool_calls:
            import json

            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            impl = impls.get(name)
            if impl is None:
                result: Any = f"error: unknown tool {name}"
            else:
                try:
                    result = impl(**args)
                except Exception as exc:  # noqa: BLE001 -- a bad tool call must
                    # not crash the whole repair/investigate attempt; the model
                    # can see the error and retry with a corrected argument.
                    # Reproduced live: PathEscape from a read-only tool (e.g.
                    # read_file on a path the model malformed) was previously
                    # UNCAUGHT here, propagating all the way through
                    # test_repairer.run() -> orchestrator._run_one() -> the
                    # outer `except Exception` in analyze(), discarding the
                    # entire attempt as INCONCLUSIVE right after a real
                    # survived=True finding. These tools are read-only (section
                    # 10.1) -- a malformed path is the model's mistake to
                    # correct, not a fatal condition for the run.
                    result = f"error: {type(exc).__name__}: {exc}"
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": str(result)}
            )
    # If the loop exhausted its rounds without the model volunteering a final
    # answer, we do NOT error -- we force the structured answer below from the
    # context gathered so far. A model that likes to keep calling tools (common
    # on smaller/free models) must not fail the whole run; the final parse call
    # omits `tools` so the model can only answer, not call more.

    # Final structured parse over the accumulated context, tools withheld so the
    # model is forced to produce the answer rather than another tool call.
    wrap = _is_list_type(output_format)
    response_format: Any = output_format
    if wrap:
        response_format, _ = _envelope_for(output_format)

    final = _call(
        client.beta.chat.completions.parse,
        messages=messages + [
            {
                "role": "user",
                "content": (
                    "Stop calling tools. Based on everything gathered above, "
                    "return the final structured result now."
                ),
            }
        ],
        response_format=response_format,
        **_common_kwargs(stage),
    )
    trace.api_call(stage, final)
    _assert_finished(final)

    parsed = final.choices[0].message.parsed
    if parsed is None:
        raise ReqGuardInternalError(f"{stage}: tool-calling run produced no structured output")
    return parsed.items if wrap else parsed  # type: ignore[return-value,attr-defined]


# ── smoke check: the B1 gate ─────────────────────────────────────────────────

def _smoke() -> int:
    """`python -m reqguard.agent.client --smoke`. Asserts call shape + policy
    without spending a token or needing a key."""
    policy.validate()

    for stage in policy.STAGES:
        kw = _common_kwargs(stage)
        assert kw["model"] == MODEL
        assert kw["max_tokens"] == MAX_TOKENS
        assert kw["reasoning_effort"] in ("low", "medium", "high"), kw
        assert kw["reasoning_effort"] == _REASONING_EFFORT[policy.effort_for(stage)]

    msgs = _messages("SYSTEM PROMPT TEXT", "volatile user content")
    assert msgs[0] == {"role": "system", "content": "SYSTEM PROMPT TEXT"}
    assert msgs[1] == {"role": "user", "content": "volatile user content"}

    # list[...] detection + envelope wrap/unwrap
    from reqguard.models import Requirement

    assert _is_list_type(list[Requirement])
    assert not _is_list_type(Requirement)
    env, item = _envelope_for(list[Requirement])
    assert item is Requirement
    assert "items" in env.model_fields

    # BASE_URL is the default (OpenRouter) fallback; CONDUIT_BASE_URL can
    # override at runtime. Just assert it's a plausible v1 endpoint.
    assert BASE_URL.rstrip("/").endswith("/v1") or "openrouter.ai" in BASE_URL, BASE_URL

    # finish_reason assertion fires on a bad reason
    class _Choice:
        finish_reason = "length"

    class _Truncated:
        choices = [_Choice()]

    try:
        _assert_finished(_Truncated())
    except ReqGuardInternalError:
        pass
    else:
        raise AssertionError("finish_reason assertion did not fire")

    print("client smoke OK:", MODEL, "| effort", dict(_REASONING_EFFORT))
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
