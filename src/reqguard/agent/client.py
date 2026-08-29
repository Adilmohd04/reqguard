"""The single Anthropic call surface for ReqGuard. Owned by Track B.

Every model call in the repository goes through this module. Tracks A, C, D and E
import nothing from `reqguard.agent`, so the deterministic core and the evaluator
run in CI with no API key.

Verified against the Anthropic Python SDK this session
(docs/03-implementation-manual.md section 1):

- `messages.parse(..., output_format=Model)` returns `resp.parsed_output`, validated.
- `thinking={"type": "adaptive"}` -- on by default on Opus 5; `display` left at its
  default (`omitted`). We never set `summarized` (section 11, blueprint section 29).
- `output_config={"effort": ...}` -- effort is nested, never a top-level kwarg.
- NO `budget_tokens` -- removed on Opus 5, sending it is a 400.
- NO assistant prefill -- a 400 on Opus 5; structured output replaces it.
- The `system` block is a single cached text block. Nothing is interpolated into
  it -- any per-run byte change invalidates the cached prefix (5x cost).
- Retries are the SDK's (2, exponential backoff, 429/5xx/connection). No `tenacity`.

`pause_turn` only arises from server-side tools and we declare none. If it appears
we raise `ReqGuardInternalError` rather than return a silently truncated answer
(section 10.3).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from reqguard.errors import ReqGuardInternalError
from reqguard.agent import policy
from reqguard.agent.policy import EFFORT, MAX_TOKENS, MODEL

__all__ = ["structured", "run_with_tools", "Trace"]

_T = TypeVar("_T", bound=BaseModel)

# Zero-arg: resolves ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN -> `ant auth login`
# profile. Never a hardcoded key. Construction does not require a credential; the
# first real call does.
_client = anthropic.Anthropic()

# Adaptive thinking, display left at the SDK default (omitted). Frozen here as a
# module constant so no call site can pass a variant.
_THINKING: dict[str, str] = {"type": "adaptive"}


class Trace(Protocol):
    """The slice of Track C's trajectory logger this module needs. The real
    object is injected by the orchestrator; this module never imports Track C."""

    def api_call(self, stage: str, response: Any) -> None: ...


class _NullTrace:
    """Used by the smoke check and by callers that have no trajectory yet."""

    def api_call(self, stage: str, response: Any) -> None:  # noqa: D401
        return None


def _system_block(system: str) -> list[dict[str, Any]]:
    """The cached system block. One text block, `cache_control` ephemeral, and
    the text is exactly `system` -- nothing is interpolated. Callers pass the
    prompt file contents verbatim; the volatile content goes in the user turn."""
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _request_kwargs(
    stage: str,
    system: str,
    user: str,
    *,
    output_format: type[BaseModel] | None = None,
    tools: list[Any] | None = None,
) -> dict[str, Any]:
    """Build the kwargs common to every ReqGuard model call.

    Pure and side-effect free so the smoke check can assert the invariants
    without a network call.
    """
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "thinking": dict(_THINKING),
        "output_config": {"effort": policy.effort_for(stage)},
        "system": _system_block(system),
        "messages": [{"role": "user", "content": user}],
    }
    if output_format is not None:
        kwargs["output_format"] = output_format
    if tools is not None:
        kwargs["tools"] = tools
    return kwargs


def _assert_not_paused(message: Any) -> None:
    if message is not None and getattr(message, "stop_reason", None) == "pause_turn":
        raise ReqGuardInternalError(
            "unexpected pause_turn -- no server-side tools are declared"
        )


def structured(
    stage: str,
    system: str,
    user: str,
    output_format: type[_T],
    trace: Trace | None = None,
) -> _T:
    """A single structured call: `messages.parse` with a Pydantic `output_format`.

    Stages 1 (analyze) and 3 (plan) use this. Returns the validated model
    instance. The response's request id and usage are handed to `trace`.
    """
    trace = trace or _NullTrace()
    resp = _client.messages.parse(**_request_kwargs(
        stage, system, user, output_format=output_format
    ))
    trace.api_call(stage, resp)
    _assert_not_paused(resp)
    return resp.parsed_output


def run_with_tools(
    stage: str,
    system: str,
    user: str,
    tools: list[Any],
    output_format: type[_T],
    trace: Trace | None = None,
) -> _T:
    """A tool-using stage driven by the SDK Tool Runner, ending in a validated
    structured output. Stages 2 (investigate) and 5 (repair) use this.

    The tool list must already be in a fixed, sorted order -- it is part of the
    cached prefix and a set that reorders between calls silently invalidates the
    cache (section 4.3). This function does not sort; `tools/registry.py` does.
    """
    trace = trace or _NullTrace()
    runner = _client.beta.messages.tool_runner(**_request_kwargs(
        stage, system, user, output_format=output_format, tools=tools
    ))
    last: Any = None
    for message in runner:
        last = message
        trace.api_call(stage, message)
    _assert_not_paused(last)
    if last is None:
        raise ReqGuardInternalError(f"{stage}: tool runner yielded no message")
    return last.parse()


# ── smoke check: the B1 gate ─────────────────────────────────────────────────

def _smoke() -> int:
    """`python -m reqguard.agent.client --smoke`. Asserts the call shape and the
    policy table without spending a token."""
    import inspect
    import re

    policy.validate()

    src = inspect.getsource(sys.modules[__name__])
    if re.search(r"^\s*(import|from)\s+tenacity\b", src, re.MULTILINE):
        raise AssertionError("client: no tenacity import -- the SDK already retries")
    if "tenacity" in sys.modules:
        raise AssertionError("client: tenacity got imported somewhere -- the SDK retries")

    for stage in policy.STAGES:
        k = _request_kwargs(stage, "SYSTEM PROMPT TEXT", "volatile user content")

        assert k["model"] == "claude-opus-5", k["model"]
        assert k["max_tokens"] == MAX_TOKENS

        assert k["thinking"] == {"type": "adaptive"}, k["thinking"]
        assert "display" not in k["thinking"], "display must be left at default"
        assert "budget_tokens" not in k["thinking"], "budget_tokens is a 400 on Opus 5"
        assert "budget_tokens" not in k, "budget_tokens must not appear anywhere"

        assert "effort" in k["output_config"], "effort must be inside output_config"
        assert "effort" not in k, "effort must NOT be a top-level kwarg"
        assert k["output_config"]["effort"] == EFFORT[stage], stage

        sysb = k["system"]
        assert isinstance(sysb, list) and len(sysb) == 1, sysb
        assert sysb[0]["type"] == "text"
        assert sysb[0]["text"] == "SYSTEM PROMPT TEXT", "nothing interpolated into system"
        assert sysb[0]["cache_control"] == {"type": "ephemeral"}, sysb[0]

        assert k["messages"] == [{"role": "user", "content": "volatile user content"}]
        assert "tool_choice" not in k, "no forced tool choice"
        assert "stop_sequences" not in k

    # the two call surfaces exist
    assert callable(_client.messages.parse)
    assert callable(_client.beta.messages.tool_runner)

    # pause_turn assertion fires
    class _Paused:
        stop_reason = "pause_turn"

    try:
        _assert_not_paused(_Paused())
    except ReqGuardInternalError:
        pass
    else:
        raise AssertionError("pause_turn assertion did not fire")

    print("client smoke OK:", MODEL, "| effort", dict(EFFORT))
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
