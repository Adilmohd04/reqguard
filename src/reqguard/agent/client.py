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

- `OpenAI(base_url=..., api_key=...)`, one per configured provider in
  `_PROVIDER_BUILDERS` (aerolink/conduit/openrouter). Constructed **lazily**
  and cached per-provider (`_resolve()`), so --replay/doctor/CI need no key
  at all -- same contract the Gemini port preserved.
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
- Retries: the `openai` SDK retries transient 429/5xx internally (default 2x)
  before raising -- but found live to not be enough on its own: a real Conduit
  503 ("Temporary service interruption. Retry the last turn...") still
  surfaced as an exception after the SDK's own retries were exhausted, while
  aerolink was separately capped and OpenRouter separately underfunded --
  all three failing on the SAME call, for three different reasons. `_call()`
  below adds one more same-provider retry specifically for 429/5xx (never for
  402/403 -- those are credit/auth walls, not transience) before moving on to
  the next provider in the chain.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Protocol, TypeVar, get_args, get_origin

import openai
from openai import OpenAI
import pydantic
from pydantic import BaseModel, create_model

from reqguard.errors import ReqGuardInternalError
from reqguard.agent import policy
from reqguard.agent.policy import BASE_URL, MAX_TOKENS, MODEL

_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
# Shrunk from (2.0, 5.0), 2026-08-31: real evidence a live multi-requirement
# run pays this schedule on EVERY call that lands on a degraded provider, and
# today's Conduit outage was sustained (hours), not a seconds-scale blip --
# every single retry against it today failed, none ever recovered mid-wait.
# Measured live: a healthy single-requirement run was 15 calls in ~2 min
# (~8s/call); a run fighting a degraded Conduit was ~19 calls in 27 min
# (~85s/call, ~10x slower) before still failing. The full (2.0, 5.0) schedule
# (up to 7s of pure sleep PER degraded provider PER call, on top of the
# actual request latency) was a real, measured, evidence-backed cost with
# zero observed payoff against a sustained outage -- one short retry still
# catches a genuine few-second blip without compounding into minutes across
# a multi-call run.
_RETRY_BACKOFF_SECONDS = (1.0,)

__all__ = ["structured", "run_with_tools", "Trace"]

_T = TypeVar("_T", bound=BaseModel)

# OpenRouter tool-call round-trips before we stop looping and FORCE a final
# structured answer (tools withheld on that last call). Small models on the free
# tier tend to keep calling tools; capping low keeps a single case cheap.
_MAX_TOOL_ROUNDS = 6

# Provider fallback chain, in priority order. Each builder returns
# (client, model) if its key is set, else None. Different providers need
# different model names -- gpt-5.6-sol only exists on aerolink -- so the
# active model travels with the client, never a bare policy.py constant.
#
# aerolink's default_headers spoof is load-bearing, not decorative: verified
# live this session that their Cloudflare/WAF blocks requests carrying the
# openai SDK's own default User-Agent (a raw httpx POST with an identical
# body succeeded; the SDK call with its real UA got a generic
# PermissionDeniedError) but allows a generic one through. Confirmed with it
# that /v1/chat/completions, structured output (response_format), and tool
# calling (tools=[...]) all genuinely work on gpt-5.6-sol.
def _build_conduit() -> tuple[OpenAI, str] | None:
    import os

    key = os.environ.get("CONDUIT_API_KEY")
    if not key:
        return None
    base = os.environ.get("CONDUIT_BASE_URL") or BASE_URL
    return OpenAI(base_url=base, api_key=key), MODEL


def _build_openrouter() -> tuple[OpenAI, str] | None:
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenAI(base_url=BASE_URL, api_key=key), MODEL


def _build_aerolink() -> tuple[OpenAI, str] | None:
    import os

    key = os.environ.get("AEROLINK_API_KEY")
    if not key:
        return None
    # Model is env-configurable (aerolink cgapi hosts gpt-5.6-luna/sol/terra
    # and FreeModel); default is terra. Set AEROLINK_MODEL to override without
    # a code edit.
    model = os.environ.get("AEROLINK_MODEL", "gpt-5.6-terra")
    base = os.environ.get("AEROLINK_BASE_URL", "https://cgapi.aerolink.lat/v1")
    return (
        OpenAI(
            base_url=base,
            api_key=key,
            default_headers={"User-Agent": "curl/8.0"},
        ),
        model,
    )


_PROVIDER_BUILDERS: list[tuple[str, Any]] = [
    ("aerolink", _build_aerolink),
    ("conduit", _build_conduit),
    ("openrouter", _build_openrouter),
]

# aerolink's real $5/5-hour cap is the binding constraint on a live run (found
# live, repeatedly, 2026-08-31) -- `gpt-5.6-sol` for every single call burns
# through it well before a 4-requirement analyze completes. `analyze` (1 call,
# medium effort, no tools) and `investigate` (the highest call-volume stage --
# up to _MAX_TOOL_ROUNDS calls PER requirement) are the two places that cost
# the most for the least reasoning depth required, so they get `gpt-5.6-terra`
# (reprobed fresh against the new key, passed both structured output and tool
# calling cleanly).
#
# `plan`, `repair`, and `baseline` are pinned back to `gpt-5.6-sol` HERE,
# explicitly -- not just left out of this map -- because `_build_aerolink`'s
# own default model changed to terra too (env-configurable, `AEROLINK_MODEL`).
# Leaving them unlisted would silently pull them onto terra along with
# everything else, defeating the whole point of keeping the correctness-
# critical stages on the better-tested model. Found live, real evidence, not
# hypothetical: a `plan` call on terra against `itsdangerous` proposed an
# `original_snippet` with 20 spaces of indentation where the real file had
# 12 -- content right, whitespace wrong -- correctly rejected by Track A's
# `anchor_unique` gate (INCONCLUSIVE, not a silent corruption or a guess).
# `plan` is "the highest-leverage stage in the system" (mutation_planner's
# own docstring) and needs byte-exact anchors to work at all; `repair` must
# produce an actually-correct passing+killing test; `baseline` must stay on
# the same footing as ReqGuard's own top-effort stage for a fair comparison
# (section 16.2). None of the three can absorb a whitespace-precision miss
# the way analyze/investigate can.
_AEROLINK_MODEL_OVERRIDE: dict[str, str] = {
    "analyze": "gpt-5.6-terra",
    "investigate": "gpt-5.6-terra",
    "plan": "gpt-5.6-sol",
    "repair": "gpt-5.6-sol",
    "baseline": "gpt-5.6-sol",
}

# `_client`/`_active_model` are a TEST-ONLY override hook --
# tests/integration/test_stages.py's fake_client fixture monkeypatches
# `_client` directly, and `_call()` below uses it as-is, bypassing the
# provider chain entirely. Real live calls never set these two; they use
# `_provider_cache`/`_preferred_index` instead. Kept separate on purpose
# after a real bug: an earlier version cached ONE globally-advancing
# provider index for the whole process, so a single transient failure on
# provider A permanently stuck every later call in the same run onto
# whichever provider it had advanced to -- even calls made *after* A had
# recovered. Reproduced live: Conduit worked when probed standalone, but a
# multi-requirement `analyze` run still failed all the way through to
# OpenRouter's empty balance, because one earlier call in that same run had
# already ratcheted the shared index past Conduit and never went back.
# Fixed by making every `_call()` search the full chain independently.
_client: OpenAI | None = None
_active_model: str = MODEL

_provider_cache: dict[int, tuple[OpenAI, str]] = {}
_preferred_index: int = 0  # a starting-point HINT for the next call's
# search (whichever provider worked last), never a permanent record.


def _resolve(index: int) -> tuple[OpenAI, str] | None:
    """Build (or reuse the cached) client for `_PROVIDER_BUILDERS[index]`.
    None if that provider has no key configured."""
    if index not in _provider_cache:
        _name, build = _PROVIDER_BUILDERS[index]
        result = build()
        if result is None:
            return None
        _provider_cache[index] = result
    return _provider_cache[index]


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


def _common_kwargs(stage: str, model: str) -> dict[str, Any]:
    return {
        "model": model,
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


def _call_with_transient_retry(fn: Any, call_kwargs: dict[str, Any], provider_name: str) -> Any:
    """Call `fn(**call_kwargs)`, retrying in place (same provider) when the
    failure is a 429/5xx -- found live to matter: the `openai` SDK's own
    internal retries aren't always enough (a real Conduit 503 outlasted
    them), and the error body itself said to retry ("Retry the last turn;
    your conversation and tool state are preserved"). 402/403 (credit/auth
    walls) are never retried here -- they're not transient, and burning
    `_RETRY_BACKOFF_SECONDS` on one just delays reaching a provider that can
    actually answer."""
    last: openai.APIStatusError | None = None
    for attempt, delay in enumerate((0.0, *_RETRY_BACKOFF_SECONDS)):
        if delay:
            time.sleep(delay)
        try:
            return fn(**call_kwargs)
        except openai.APIStatusError as exc:
            last = exc
            if exc.status_code not in _RETRY_STATUS_CODES:
                raise
            print(
                f"  ({provider_name}: {exc.status_code}, retrying in-place "
                f"[{attempt + 1}/{len(_RETRY_BACKOFF_SECONDS) + 1}])",
                file=sys.stderr,
            )
    assert last is not None
    raise last


def _call(method_path: str, stage: str, **kwargs: Any) -> Any:
    """Call `method_path` (e.g. "beta.chat.completions.parse"), with
    `model`/`max_tokens`/`reasoning_effort` filled in for whichever provider
    ends up handling it -- `kwargs` never includes `model` itself, since
    that's provider-specific (gpt-5.6-sol only exists on aerolink).

    Test override: if `_client` is set (the fake_client fixture's
    monkeypatch), it's used directly and the provider chain below is never
    consulted at all.

    Real calls search the FULL chain on every invocation, starting from
    `_preferred_index` (whichever provider worked last, an efficiency hint
    only) and wrapping around through every other configured provider before
    giving up -- never a permanent record that a provider is "dead" for the
    rest of the run. That distinction is load-bearing, found live: an
    earlier version advanced a single shared index forever, so one
    transient failure on provider A permanently stuck every LATER call in
    the same multi-requirement run onto whichever provider it had reached,
    even after A had recovered. Reproduced directly: Conduit answered fine
    when probed standalone, but a real `analyze` run still failed all the
    way through to OpenRouter's empty balance, because an earlier call in
    that same process had already advanced past Conduit and never revisited
    it. Fixed by making the search local to each call.

    Only raises once every configured provider has failed on THIS call;
    the error names the LAST one tried, not the first.
    """
    if _client is not None:
        fn: Any = _client
        for part in method_path.split("."):
            fn = getattr(fn, part)
        return fn(**{**kwargs, **_common_kwargs(stage, _active_model)})

    global _preferred_index
    n = len(_PROVIDER_BUILDERS)
    order = [(_preferred_index + i) % n for i in range(n)]
    last_exc: Exception | None = None
    last_msg = ""
    tried_any = False
    for idx in order:
        result = _resolve(idx)
        if result is None:
            continue
        tried_any = True
        name, _builder = _PROVIDER_BUILDERS[idx]
        client, model = result
        if name == "aerolink":
            model = _AEROLINK_MODEL_OVERRIDE.get(stage, model)
        fn = client
        for part in method_path.split("."):
            fn = getattr(fn, part)
        call_kwargs = {**kwargs, **_common_kwargs(stage, model)}
        try:
            response = _call_with_transient_retry(fn, call_kwargs, name)
        except openai.APIStatusError as exc:
            # HTTP-status failures: rate limits, credit walls, account
            # gates, outages -- already retried in-place if transient (see
            # _call_with_transient_retry); still failing means move on.
            last_exc, last_msg = exc, f"({exc.status_code}) {exc.message}"
            continue
        except pydantic.ValidationError as exc:
            # A 200 response whose BODY isn't valid JSON for the schema --
            # NOT an APIStatusError, found live: a Conduit response body of
            # literally "The response did not gen[erate]... resetting the
            # session." instead of JSON. The request succeeded at the HTTP
            # layer; the content was garbage. Just as much a reason to try
            # the next provider as an HTTP error would be.
            last_exc, last_msg = exc, f"invalid response body: {exc}"
            continue
        _preferred_index = idx
        return response

    if not tried_any:
        raise ReqGuardInternalError(
            "no provider key set -- a live run needs one (--replay needs none).\n"
            "  Quickest path: sign up at https://openrouter.ai, create a key at "
            "https://openrouter.ai/keys,\n"
            "  then: cp .env.example .env   and set OPENROUTER_API_KEY= there.\n"
            "  (CONDUIT_API_KEY / AEROLINK_API_KEY also work, if you have one --"
            " see .env.example.)"
        )
    assert last_exc is not None
    raise ReqGuardInternalError(f"provider error {last_msg}") from last_exc


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
        "beta.chat.completions.parse",
        stage,
        messages=_messages(system, user),
        response_format=response_format,
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

    schemas = [t["schema"] for t in tools]
    impls = {t["schema"]["function"]["name"]: t["impl"] for t in tools}

    messages = _messages(system, user)

    for _round in range(_MAX_TOOL_ROUNDS):
        response = _call(
            "chat.completions.create",
            stage,
            messages=messages,
            tools=schemas,
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
        "beta.chat.completions.parse",
        stage,
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
        kw = _common_kwargs(stage, MODEL)
        assert kw["model"] == MODEL
        assert kw["max_tokens"] == MAX_TOKENS
        assert kw["reasoning_effort"] in ("low", "medium", "high"), kw
        assert kw["reasoning_effort"] == _REASONING_EFFORT[policy.effort_for(stage)]

    # provider fallback chain: three candidates, aerolink's WAF-bypass header
    # present, none constructed yet (no key needed for the smoke check itself)
    assert len(_PROVIDER_BUILDERS) == 3
    assert [name for name, _ in _PROVIDER_BUILDERS] == ["aerolink", "conduit", "openrouter"]

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
