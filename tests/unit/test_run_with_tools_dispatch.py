"""Unit tests for `run_with_tools`'s tool-dispatch loop in `agent/client.py`.
Owned by Track B.

`tests/integration/test_stages.py`'s `fake_client` fixture always returns a
message with `tool_calls=None` from `chat.completions.create` -- so every
existing stage test breaks out of the tool-dispatch loop on round one and
never actually calls a real `impl(**args)`. The exact code path a live bug
hit (a malformed tool call crashing the whole attempt -- see commit 1518fea)
had zero coverage, before or after the fix. These tests exercise that loop
directly: a scripted `chat.completions.create` that DOES request a tool call.
"""

from __future__ import annotations

import pytest

from reqguard.agent import client as C


class _FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = type("_Fn", (), {"name": name, "arguments": arguments})()

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class _FakeMessage:
    def __init__(self, content: str = "", tool_calls=None, parsed=None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.parsed = parsed


class _FakeResponse:
    def __init__(self, message: _FakeMessage, finish_reason: str = "stop") -> None:
        self.choices = [type("_Choice", (), {"message": message, "finish_reason": finish_reason})()]
        self.usage = None


class _ScriptedClient:
    """`chat.completions.create` returns each of `create_responses` in order
    (one per round); `beta.chat.completions.parse` always returns
    `final_parsed` wrapped in a response."""

    def __init__(self, create_responses: list[_FakeResponse], final_parsed: object) -> None:
        self._create_responses = list(create_responses)
        self._final_parsed = final_parsed
        self.create_call_count = 0
        self.messages_seen: list[list[dict]] = []

        completions = self

        class _Chat:
            completions = self  # type: ignore[assignment]

        self.chat = type("_Chat", (), {"completions": self})()
        self.beta = type("_Beta", (), {"chat": type("_C", (), {"completions": self})()})()

    def create(self, **kwargs):
        self.messages_seen.append(kwargs.get("messages", []))
        self.create_call_count += 1
        return self._create_responses.pop(0)

    def parse(self, **kwargs):
        return _FakeResponse(_FakeMessage(parsed=self._final_parsed))


class _Output:
    """Minimal stand-in for a structured output_format -- run_with_tools only
    needs `_is_list_type` to say no (it's not `list[...]`), which any
    non-generic type satisfies."""


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(C, "_client", None)
    yield


def test_a_tool_that_raises_does_not_crash_the_whole_call(monkeypatch):
    """Regression for the PathEscape crash (commit 1518fea): a bad argument
    from the model must come back as an error string the model can see and
    retry, never propagate up through run_with_tools."""

    def boom(**_kwargs):
        raise ValueError("simulated PathEscape: path escapes sandbox root")

    tools = [
        {
            "schema": {"type": "function", "function": {"name": "read_file", "parameters": {}}},
            "impl": boom,
        }
    ]

    call_then_stop = [
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[_FakeToolCall("call_1", "read_file", '{"path": "../../etc/passwd"}')],
            ),
            finish_reason="tool_calls",
        ),
        _FakeResponse(_FakeMessage(content="giving up on that path", tool_calls=None)),
    ]
    fake = _ScriptedClient(call_then_stop, final_parsed=_Output())
    monkeypatch.setattr(C, "_client", fake)

    result = C.run_with_tools("investigate", "system", "user", tools, _Output)

    assert isinstance(result, _Output)
    assert fake.create_call_count == 2, "must have continued to a second round, not crashed"
    # The error must have been fed back as a tool-role message, not raised.
    second_round_messages = fake.messages_seen[1]
    tool_messages = [m for m in second_round_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "error: ValueError" in tool_messages[0]["content"]
    assert "simulated PathEscape" in tool_messages[0]["content"]


def test_an_unknown_tool_name_also_comes_back_as_an_error_not_a_crash(monkeypatch):
    tools: list = []  # nothing registered -- impls.get(name) is None
    call_then_stop = [
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[_FakeToolCall("call_1", "does_not_exist", "{}")],
            ),
            finish_reason="tool_calls",
        ),
        _FakeResponse(_FakeMessage(content="ok", tool_calls=None)),
    ]
    fake = _ScriptedClient(call_then_stop, final_parsed=_Output())
    monkeypatch.setattr(C, "_client", fake)

    result = C.run_with_tools("repair", "system", "user", tools, _Output)

    assert isinstance(result, _Output)
    tool_messages = [m for m in fake.messages_seen[1] if m.get("role") == "tool"]
    assert "error: unknown tool does_not_exist" in tool_messages[0]["content"]


def test_a_successful_tool_call_dispatches_to_the_matching_impl_with_parsed_args(monkeypatch):
    seen_args: dict = {}

    def read_file(path: str, start: int = 1) -> str:
        seen_args["path"] = path
        seen_args["start"] = start
        return "L1: hello"

    tools = [
        {
            "schema": {"type": "function", "function": {"name": "read_file", "parameters": {}}},
            "impl": read_file,
        }
    ]
    call_then_stop = [
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[_FakeToolCall("call_1", "read_file", '{"path": "src/a.py", "start": 3}')],
            ),
            finish_reason="tool_calls",
        ),
        _FakeResponse(_FakeMessage(content="done", tool_calls=None)),
    ]
    fake = _ScriptedClient(call_then_stop, final_parsed=_Output())
    monkeypatch.setattr(C, "_client", fake)

    result = C.run_with_tools("investigate", "system", "user", tools, _Output)

    assert isinstance(result, _Output)
    assert seen_args == {"path": "src/a.py", "start": 3}
    tool_messages = [m for m in fake.messages_seen[1] if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "L1: hello"
    assert tool_messages[0]["tool_call_id"] == "call_1"


def test_exhausting_max_rounds_still_forces_a_final_answer(monkeypatch):
    """A model that likes to keep calling tools (documented in run_with_tools
    as common on smaller/free models) must not fail the whole run once
    _MAX_TOOL_ROUNDS is hit -- the loop stops issuing new tool-calling
    rounds and forces a final structured answer from whatever was gathered.
    This is a real, deliberate design choice (client.py's own comment) that
    had never actually been exercised by a test."""

    def read_file(**_kwargs) -> str:
        return "some content"

    tools = [
        {
            "schema": {"type": "function", "function": {"name": "read_file", "parameters": {}}},
            "impl": read_file,
        }
    ]
    # The model NEVER stops requesting a tool call -- one scripted response
    # per round, all of them requesting one, more than _MAX_TOOL_ROUNDS deep.
    always_calls_a_tool = [
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[_FakeToolCall(f"call_{i}", "read_file", "{}")],
            ),
            finish_reason="tool_calls",
        )
        for i in range(C._MAX_TOOL_ROUNDS + 3)
    ]
    fake = _ScriptedClient(always_calls_a_tool, final_parsed=_Output())
    monkeypatch.setattr(C, "_client", fake)

    result = C.run_with_tools("investigate", "system", "user", tools, _Output)

    assert isinstance(result, _Output), "must force a final answer, not raise or hang"
    assert fake.create_call_count == C._MAX_TOOL_ROUNDS, (
        "must stop issuing new tool-calling rounds at the cap, not run forever"
    )


def test_malformed_json_arguments_do_not_crash_either(monkeypatch):
    """`json.loads` on a truncated/garbled `arguments` string is itself a
    failure mode a small/free model can produce -- covered separately from
    the impl-raises case since it's caught one line earlier in the loop."""
    called_with: dict = {}

    def read_file(**kwargs) -> str:
        called_with.update(kwargs)
        return "ok"

    tools = [
        {
            "schema": {"type": "function", "function": {"name": "read_file", "parameters": {}}},
            "impl": read_file,
        }
    ]
    call_then_stop = [
        _FakeResponse(
            _FakeMessage(
                content="",
                tool_calls=[_FakeToolCall("call_1", "read_file", "{not valid json")],
            ),
            finish_reason="tool_calls",
        ),
        _FakeResponse(_FakeMessage(content="done", tool_calls=None)),
    ]
    fake = _ScriptedClient(call_then_stop, final_parsed=_Output())
    monkeypatch.setattr(C, "_client", fake)

    result = C.run_with_tools("investigate", "system", "user", tools, _Output)

    assert isinstance(result, _Output)
    assert called_with == {}, "malformed arguments must fall back to {} args, not crash"
