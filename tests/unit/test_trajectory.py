"""Unit tests for the trajectory logger (task C1).

Acceptance (crew/tasks.yaml C1):
  - append-only JSONL, one event per line, round-trips
  - types: tool_call, tool_result, decision, api_call, verdict, error
  - api_call records request_id and the full usage object
  - NO raw thinking / chain-of-thought is ever written (Doc 2 §29)
  - cache_read_input_tokens == 0 on the 2nd requirement emits a cache_miss error
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reqguard.tracing.trajectory import (
    EventType,
    Trajectory,
    read_events,
)


# ── append-only JSONL, one event per line, round-trips ─────────────────────

def test_one_event_per_line(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "trajectory.jsonl")
    trace.tool_call("search_code", {"pattern": "def ship"}, stage="investigate")
    trace.decision("Selected ship_order", stage="investigate")
    trace.verdict({"survived": True}, stage="execute")

    raw = (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    assert len(lines) == 3
    # each line is independently valid JSON
    for ln in lines:
        json.loads(ln)


def test_round_trips(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.tool_call("read_file", {"path": "a.py"}, requirement_id="REQ-01")
    trace.tool_result("read_file", "42 lines", requirement_id="REQ-01")

    events = trace.read()
    assert [e["type"] for e in events] == ["tool_call", "tool_result"]
    assert events[0]["tool"] == "read_file"
    assert events[0]["input"] == {"path": "a.py"}
    assert events[0]["requirement_id"] == "REQ-01"
    assert events[1]["result_summary"] == "42 lines"
    # read_events free function returns the same thing
    assert list(read_events(trace.path)) == events


def test_append_only_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    Trajectory(path).decision("first", stage="analyze")
    # a *new* Trajectory over the same path must not truncate the file
    Trajectory(path).decision("second", stage="analyze")

    events = list(read_events(path))
    assert len(events) == 2
    assert events[0]["rationale"] == "first"
    assert events[1]["rationale"] == "second"


def test_steps_are_monotonic(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    for _ in range(5):
        trace.decision("x", stage="plan")
    steps = [e["step"] for e in trace.read()]
    assert steps == [1, 2, 3, 4, 5]


def test_every_record_has_ts_and_type(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.tool_call("list_files")
    (event,) = trace.read()
    assert event["type"] == "tool_call"
    assert event["ts"].endswith("Z")


# ── all six event types are supported ──────────────────────────────────────

def test_all_six_event_types(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.tool_call("search_code", {"pattern": "x"})
    trace.tool_result("search_code", "3 hits")
    trace.decision("chose the guard")
    trace.api_call("claude-opus-5", "req_1", {"input_tokens": 10})
    trace.verdict({"survived": False})
    trace.error("boom", "something broke")

    types = [e["type"] for e in trace.read()]
    assert types == [
        "tool_call",
        "tool_result",
        "decision",
        "api_call",
        "verdict",
        "error",
    ]


def test_unknown_event_type_rejected(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    with pytest.raises(ValueError):
        trace.emit("thinking_dump")  # type: ignore[arg-type]


# ── api_call records request_id and the full usage object ──────────────────

def test_api_call_records_request_id_and_full_usage(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    usage = {
        "input_tokens": 12481,
        "output_tokens": 903,
        "cache_read_input_tokens": 11200,
        "cache_creation_input_tokens": 0,
    }
    trace.api_call("claude-opus-5", "req_018Ee", usage, stage="plan")

    (event,) = trace.read()
    assert event["type"] == "api_call"
    assert event["model"] == "claude-opus-5"
    assert event["request_id"] == "req_018Ee"
    # full usage preserved verbatim — cost is summed from this, not estimated
    assert event["usage"] == usage


def test_api_call_coerces_object_usage(tmp_path: Path) -> None:
    class FakeUsage:
        def model_dump(self) -> dict[str, int]:
            return {"input_tokens": 5, "cache_read_input_tokens": 4}

    trace = Trajectory(tmp_path / "t.jsonl")
    trace.api_call("claude-opus-5", "req_x", FakeUsage())
    (event,) = trace.read()
    assert event["usage"] == {"input_tokens": 5, "cache_read_input_tokens": 4}


# ── NO raw thinking / chain-of-thought is ever written (Doc 2 §29) ─────────

def test_reasoning_fields_are_dropped(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.decision(
        "concise rationale",
        thinking="secret internal monologue that must never be logged",
        chain_of_thought=["step 1", "step 2"],
        reasoning="more hidden reasoning",
        scratchpad="notes",
    )
    raw = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    assert "secret internal monologue" not in raw
    assert "hidden reasoning" not in raw

    (event,) = trace.read()
    assert event["rationale"] == "concise rationale"
    for banned in ("thinking", "chain_of_thought", "reasoning", "scratchpad"):
        assert banned not in event


def test_reasoning_dropped_from_nested_payloads(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.tool_result(
        "investigate",
        "ok",
        detail={"sites": ["a"], "thinking": "leak", "cot": "leak2"},
    )
    (event,) = trace.read()
    assert event["detail"]["sites"] == ["a"]
    assert "thinking" not in event["detail"]
    assert "cot" not in event["detail"]


def test_reasoning_dropped_from_usage_object(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.api_call(
        "claude-opus-5",
        "req_1",
        {"input_tokens": 1, "reasoning_tokens_transcript": "leak"},
    )
    (event,) = trace.read()
    assert "reasoning_tokens_transcript" not in event["usage"]


# ── §11.3 cache-miss alarm ─────────────────────────────────────────────────

def test_cache_miss_emitted_on_second_call_when_zero(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    # first call: a zero cache read is legitimate (nothing cached yet)
    trace.api_call(
        "claude-opus-5", "req_1", {"cache_read_input_tokens": 0},
        requirement_id="REQ-01",
    )
    # second call with zero cache read: the cached prefix was invalidated
    trace.api_call(
        "claude-opus-5", "req_2", {"cache_read_input_tokens": 0},
        requirement_id="REQ-02",
    )

    events = trace.read()
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["kind"] == "cache_miss"
    assert errors[0]["requirement_id"] == "REQ-02"


def test_no_cache_miss_when_first_call_zero(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.api_call("claude-opus-5", "req_1", {"cache_read_input_tokens": 0})
    assert not [e for e in trace.read() if e["type"] == "error"]


def test_no_cache_miss_when_second_call_has_cache_read(tmp_path: Path) -> None:
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.api_call("claude-opus-5", "req_1", {"cache_read_input_tokens": 0})
    trace.api_call("claude-opus-5", "req_2", {"cache_read_input_tokens": 5000})
    assert not [e for e in trace.read() if e["type"] == "error"]


def test_cache_miss_when_field_absent(tmp_path: Path) -> None:
    # a missing cache_read_input_tokens is treated as 0 (same regression)
    trace = Trajectory(tmp_path / "t.jsonl")
    trace.api_call("claude-opus-5", "req_1", {"input_tokens": 1})
    trace.api_call("claude-opus-5", "req_2", {"input_tokens": 1})
    errors = [e for e in trace.read() if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["kind"] == "cache_miss"


# ── the logger creates its parent directory ────────────────────────────────

def test_parent_directory_created(tmp_path: Path) -> None:
    path = tmp_path / "reqguard-report" / "nested" / "trajectory.jsonl"
    Trajectory(path).decision("hi")
    assert path.exists()
