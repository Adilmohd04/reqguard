"""Unit tests for the provider-fallback chain in `agent/client.py`. Owned by
Track B.

No API key, no spend, no network — every provider is a fake object patched
into `client._PROVIDER_BUILDERS`. These exist because two real bugs shipped
into a live run with zero coverage catching either of them:

1. **The sticky-index bug** (fixed in commit fc53fe1): `_call()` used to
   advance a single shared `_preferred_index` and never revisit an earlier
   provider, so one transient failure on provider A permanently stuck every
   *later* call in the same process onto whichever provider the index had
   last reached — even after A had recovered. Reproduced live: Conduit
   answered fine standalone, but a real multi-requirement `analyze` run kept
   failing all the way through to OpenRouter's empty balance regardless.
   `test_call_wraps_the_full_chain_even_after_an_earlier_call_advanced_the_hint`
   below is the direct regression test.
2. **The swallowed-ValidationError bug** (fixed in commit cb3c0ac): Conduit
   returned HTTP 200 with a plain-text body instead of JSON for a structured-
   output call, raising `pydantic.ValidationError` — not an
   `openai.APIStatusError` — which `_call()`'s except clause didn't catch, so
   a "200 with garbage content" crashed the whole call instead of falling
   through. `test_call_falls_through_on_a_200_with_an_invalid_body` covers it.
3. **The no-in-place-retry gap** (fixed in commit e357df3): a real Conduit
   `503` ("Retry the last turn...") and `429` ("Free shared capacity is
   busy...") were treated as immediately fatal for that provider on that
   call, with no retry — found live when all three configured providers
   failed the SAME call for three different reasons at once. Covered by the
   `_call_with_transient_retry` tests below.

Both bugs were silent until a real live run hit them — this file is what
should have existed before that, not after.
"""

from __future__ import annotations

import httpx
import openai
import pydantic
import pytest

from reqguard.agent import client as C
from reqguard.errors import ReqGuardInternalError


# ── fakes ────────────────────────────────────────────────────────────────


def _status_error(code: int, msg: str = "boom") -> openai.APIStatusError:
    resp = httpx.Response(code, request=httpx.Request("POST", "https://example.test"))
    return openai.APIStatusError(msg, response=resp, body=None)


def _validation_error() -> pydantic.ValidationError:
    class _M(pydantic.BaseModel):
        x: int

    try:
        _M(x="not-an-int")
    except pydantic.ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")  # pragma: no cover


class _Queue:
    """A callable that pops one scripted outcome per call: an Exception
    instance to raise, or any other value to return."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.n_calls = 0

    def __call__(self, **_kwargs):
        self.n_calls += 1
        if not self._outcomes:
            raise AssertionError("fake provider called more times than scripted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _fake_provider(name: str, outcomes: list) -> tuple:
    """A `(name, builder)` pair for `_PROVIDER_BUILDERS`. `builder()` returns
    an object exposing both call surfaces `_call` dispatches through
    (`chat.completions.create` and `beta.chat.completions.parse`) backed by
    the SAME queue, plus the queue itself for assertions."""
    queue = _Queue(outcomes)
    fn = queue

    class _Completions:
        create = staticmethod(fn)
        parse = staticmethod(fn)

    class _Chat:
        completions = _Completions()

    class _Beta:
        chat = _Chat()

    class _Fake:
        chat = _Chat()
        beta = _Beta()

    def builder():
        return (_Fake(), f"model-{name}")

    return name, builder, queue


@pytest.fixture(autouse=True)
def _reset_provider_state(monkeypatch):
    """Every test gets a clean slate: no cached clients, no carried-over
    preference. Mirrors what a fresh process actually starts with."""
    monkeypatch.setattr(C, "_client", None)
    monkeypatch.setattr(C, "_provider_cache", {})
    monkeypatch.setattr(C, "_preferred_index", 0)
    # Real backoff is 2s/5s -- fine live, far too slow for a unit test.
    monkeypatch.setattr(C, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    yield


def _install(monkeypatch, *providers: tuple) -> list:
    """providers: `(name, outcomes)` pairs, in chain order. Returns the list
    of `_Queue`s (one per provider) so tests can assert call counts."""
    builders = []
    queues = []
    for name, outcomes in providers:
        n, builder, queue = _fake_provider(name, outcomes)
        builders.append((n, builder))
        queues.append(queue)
    monkeypatch.setattr(C, "_PROVIDER_BUILDERS", builders)
    return queues


# ── the sticky-index regression ─────────────────────────────────────────


def test_call_wraps_the_full_chain_even_after_an_earlier_call_advanced_the_hint(monkeypatch):
    """Call 1: A fails (non-retryable), B succeeds -> the hint moves to B.
    Call 2: B now fails, C now fails too -> the search must still wrap back
    to A rather than treating A as permanently dead. This is the exact shape
    of the live bug: an earlier failure must never black out a provider for
    the rest of the process."""
    resp_b1 = object()
    resp_a2 = object()
    queues = _install(
        monkeypatch,
        ("A", [_status_error(402), resp_a2]),  # fails call 1, succeeds call 2
        ("B", [resp_b1, _status_error(402)]),  # succeeds call 1, fails call 2
        ("C", [_status_error(402)]),  # fails call 2 (only tried once)
    )
    qa, qb, qc = queues

    out1 = C._call("chat.completions.create", "analyze")
    assert out1 is resp_b1
    assert C._preferred_index == 1  # hint now points at B

    out2 = C._call("chat.completions.create", "analyze")
    assert out2 is resp_a2, "must have wrapped back to A, not stayed stuck past it"
    assert qa.n_calls == 2
    assert qb.n_calls == 2
    assert qc.n_calls == 1


# ── the swallowed-ValidationError regression ────────────────────────────


def test_call_falls_through_on_a_200_with_an_invalid_body(monkeypatch):
    """A 200 response whose body doesn't validate against the expected
    schema must be treated as a reason to try the next provider, exactly
    like an HTTP-level failure -- not an uncaught crash."""
    good = object()
    _install(
        monkeypatch,
        ("conduit", [_validation_error()]),
        ("openrouter", [good]),
    )
    out = C._call("beta.chat.completions.parse", "analyze")
    assert out is good


def test_call_raises_after_every_provider_fails(monkeypatch):
    _install(
        monkeypatch,
        ("A", [_status_error(402, "no credits")]),
        ("B", [_validation_error()]),
    )
    with pytest.raises(ReqGuardInternalError) as ei:
        C._call("chat.completions.create", "analyze")
    # Names the LAST provider tried, not the first.
    assert "invalid response body" in str(ei.value)


def test_call_raises_a_clear_error_when_no_provider_is_configured(monkeypatch):
    monkeypatch.setattr(C, "_PROVIDER_BUILDERS", [("A", lambda: None), ("B", lambda: None)])
    with pytest.raises(ReqGuardInternalError, match="no provider key set"):
        C._call("chat.completions.create", "analyze")


# ── in-place transient retry ────────────────────────────────────────────


def test_transient_retry_recovers_from_5xx_on_the_same_provider(monkeypatch):
    good = object()
    queue = _Queue([_status_error(503), _status_error(503), good])
    out = C._call_with_transient_retry(queue, {}, "conduit")
    assert out is good
    assert queue.n_calls == 3


def test_transient_retry_recovers_from_429(monkeypatch):
    good = object()
    queue = _Queue([_status_error(429, "rate limited"), good])
    out = C._call_with_transient_retry(queue, {}, "conduit")
    assert out is good
    assert queue.n_calls == 2


def test_transient_retry_gives_up_after_exhausting_the_backoff_schedule(monkeypatch):
    # 1 initial + len(_RETRY_BACKOFF_SECONDS) retries, all failing -> raises.
    n = 1 + len(C._RETRY_BACKOFF_SECONDS)
    queue = _Queue([_status_error(503) for _ in range(n)])
    with pytest.raises(openai.APIStatusError):
        C._call_with_transient_retry(queue, {}, "conduit")
    assert queue.n_calls == n


def test_transient_retry_never_retries_a_credit_or_auth_wall(monkeypatch):
    """402/403 are not transience -- retrying just burns the backoff window
    before reaching a provider that could actually answer."""
    queue = _Queue([_status_error(402, "no credits"), object()])
    with pytest.raises(openai.APIStatusError):
        C._call_with_transient_retry(queue, {}, "openrouter")
    assert queue.n_calls == 1, "must not have consumed the second scripted outcome"
