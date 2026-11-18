"""Regression tests for the transient-connection-error handling in
`reqguard.agent.client`. Owned by Track B (same file ownership as client.py).

**Why this file exists (found live, 2026-09-01):** a real `doctor` run against
case01 died on the very first LLM call with a raw, unwrapped
`openai.APIConnectionError` (`[Errno 11001] getaddrinfo failed` -- a DNS
blip). `_call_with_transient_retry` and `_call`'s per-provider fallback loop
only caught `openai.APIStatusError` and `pydantic.ValidationError` -- a
connection-level failure fell straight through both, skipped every retry,
skipped every other configured provider, and propagated as a raw traceback
instead of the clean `ReqGuardError` the CLI knows how to print. Fixed in
`client.py`; this file is the regression test that failure mode needed and
did not have.

These tests exercise `_call`'s REAL provider-fallback loop directly (not the
`fake_client` fixture in `test_stages.py`, which patches `_client` and takes
a shortcut that bypasses the provider chain entirely -- exactly the code path
that had the bug).
"""

from __future__ import annotations

from typing import Any

import httpx2
import openai
import pytest

from reqguard.agent import client as client_mod
from reqguard.errors import ReqGuardInternalError


def _connection_error(message: str = "getaddrinfo failed") -> openai.APIConnectionError:
    """A real `openai.APIConnectionError`, not a stand-in -- the SDK class
    itself has to be what's caught, so a hand-rolled lookalike would not
    prove anything."""
    request = httpx2.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.APIConnectionError(message=message, request=request)


class _FlakyCallable:
    """Raises a connection error `fail_times` times, then returns `result`."""

    def __init__(self, fail_times: int, result: Any = "ok") -> None:
        self.fail_times = fail_times
        self.result = result
        self.calls = 0

    def __call__(self, **_kwargs: Any) -> Any:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _connection_error()
        return self.result


class _AlwaysFails:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise _connection_error(f"attempt {self.calls}")


@pytest.fixture(autouse=True)
def _isolated_provider_state(monkeypatch):
    """`_provider_cache` / `_preferred_index` are module-level and shared
    across the whole process (by design -- see client.py's own comment on
    why a per-call-local search matters). Reset them per test so no test can
    leak a cached fake provider into another."""
    monkeypatch.setattr(client_mod, "_provider_cache", {})
    monkeypatch.setattr(client_mod, "_preferred_index", 0)
    monkeypatch.setattr(client_mod, "_client", None)


# ── _call_with_transient_retry ──────────────────────────────────────────────


def test_transient_retry_recovers_from_one_connection_error():
    """A DNS blip that clears on the next attempt must not fail the call --
    this is the exact shape of the live failure: the first attempt hit
    `getaddrinfo failed`, and there was no reason a retry couldn't succeed."""
    fn = _FlakyCallable(fail_times=1, result="recovered")
    result = client_mod._call_with_transient_retry(fn, {}, "test-provider")
    assert result == "recovered"
    assert fn.calls == 2


def test_transient_retry_reraises_a_connection_error_that_never_clears():
    """A sustained outage must still surface as the real
    `openai.APIConnectionError` after retries are exhausted -- not be
    swallowed, and not be silently converted into something else."""
    fn = _AlwaysFails()
    with pytest.raises(openai.APIConnectionError):
        client_mod._call_with_transient_retry(fn, {}, "test-provider")
    assert fn.calls == 2  # one attempt + one retry, per _RETRY_BACKOFF_SECONDS


# ── _call's per-provider fallback loop ──────────────────────────────────────


def _make_fake_provider(fn: Any) -> Any:
    """A minimal object satisfying the `method_path.split('.')` attribute
    walk `_call` does (e.g. `beta.chat.completions.parse`), routing every
    such call to `fn`."""
    completions = type("_Completions", (), {"parse": staticmethod(fn), "create": staticmethod(fn)})()
    beta = type("_Beta", (), {"chat": type("_Chat", (), {"completions": completions})()})()
    chat = type("_Chat", (), {"completions": completions})()
    return type("_FakeProvider", (), {"beta": beta, "chat": chat})()


def test_call_falls_through_to_the_next_provider_on_a_sustained_connection_error(
    monkeypatch,
):
    """The bug, reproduced directly: provider A is unreachable for the whole
    call (every retry also fails); provider B is healthy. `_call` must reach
    B's answer, not die on A's `APIConnectionError`."""
    dead = _AlwaysFails()
    healthy = _FlakyCallable(fail_times=0, result="from-provider-b")

    def _build_dead():
        return _make_fake_provider(dead), "model-a"

    def _build_healthy():
        return _make_fake_provider(healthy), "model-b"

    monkeypatch.setattr(
        client_mod,
        "_PROVIDER_BUILDERS",
        [("dead-provider", _build_dead), ("healthy-provider", _build_healthy)],
    )

    result = client_mod._call("beta.chat.completions.parse", "analyze")
    assert result == "from-provider-b"
    assert dead.calls == 2  # both attempts on the dead provider were made
    assert healthy.calls == 1  # then it moved on and succeeded first try


def test_call_wraps_an_all_providers_unreachable_failure_as_a_clean_reqguard_error(
    monkeypatch,
):
    """When every configured provider is unreachable, the caller (the CLI's
    `except ReqGuardError` handler) must get a typed, catchable error -- not
    a raw `openai.APIConnectionError` traceback. This is exactly what the
    live run produced before the fix: a bare SDK exception past every
    handler in main.py."""
    dead_a = _AlwaysFails()
    dead_b = _AlwaysFails()

    monkeypatch.setattr(
        client_mod,
        "_PROVIDER_BUILDERS",
        [
            ("provider-a", lambda: (_make_fake_provider(dead_a), "model-a")),
            ("provider-b", lambda: (_make_fake_provider(dead_b), "model-b")),
        ],
    )

    with pytest.raises(ReqGuardInternalError, match="connection error"):
        client_mod._call("beta.chat.completions.parse", "analyze")

    # Both providers were genuinely tried -- this is a fallback failure, not
    # a short-circuit that gave up after the first provider.
    assert dead_a.calls == 2
    assert dead_b.calls == 2


def test_call_treats_a_provider_with_no_key_as_absent_not_as_a_connection_error(
    monkeypatch,
):
    """A provider builder returning `None` (no API key configured) must be
    skipped silently, exactly as before this change -- this fix must not
    turn "not configured" into "unreachable"."""
    healthy = _FlakyCallable(fail_times=0, result="ok")

    monkeypatch.setattr(
        client_mod,
        "_PROVIDER_BUILDERS",
        [
            ("unconfigured", lambda: None),
            ("healthy-provider", lambda: (_make_fake_provider(healthy), "model-b")),
        ],
    )

    result = client_mod._call("beta.chat.completions.parse", "analyze")
    assert result == "ok"
