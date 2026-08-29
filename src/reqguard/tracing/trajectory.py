"""Append-only JSONL trajectory logger.

Owned by Track C (Evidence Layer). Tracks A and B both call it; C reads the
records back to sum cost and render the report.

Blueprint §29 is a hard constraint (docs/03-implementation-manual.md §11):
log **observable actions and concise rationales**, never a hidden reasoning
transcript. This module is the enforcement point. The API is deliberately
shaped so the wrong thing *cannot* be logged:

- there is no method that accepts free-form "thinking" / chain-of-thought;
- every event is built from a fixed, named set of fields, and any caller-
  supplied key whose name looks like a reasoning transcript is dropped before
  the line is written (`_strip_reasoning`).

A `rationale` is a single model-authored sentence produced as structured data
(the `rationale: str` field on the stage models) — not a scraped monologue —
so it is allowed and first-class here.

Format (one JSON object per line):

    {"step":12,"ts":"2026-08-29T10:14:02Z","requirement_id":"REQ-007",
     "stage":"investigate","type":"tool_call","tool":"search_code",
     "input":{"pattern":"def ship"},"result_summary":"3 hits"}

Cost in the report is summed from `api_call` records, never estimated from a
price table (§11.2). Each `api_call` carries `request_id` and the full `usage`
object.

§11.3 cache-regression alarm: the second requirement of a run whose api_call
reports `cache_read_input_tokens == 0` means the cached system prefix was
invalidated — a silent ~5× cost regression. `api_call` emits a `cache_miss`
error event automatically in that case.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

__all__ = ["EventType", "Trajectory", "read_events"]

EventType = Literal[
    "tool_call",
    "tool_result",
    "decision",
    "api_call",
    "verdict",
    "error",
]

_EVENT_TYPES: frozenset[str] = frozenset(
    ("tool_call", "tool_result", "decision", "api_call", "verdict", "error")
)

# Field names that would smuggle a reasoning transcript into the log. Blueprint
# §29 forbids raw thinking / chain-of-thought; if a caller hands us a field that
# looks like one, we drop it rather than trust the caller to have not. The check
# is on substrings so `thinking`, `raw_thinking`, `chain_of_thought`, `cot`,
# `reasoning`, `scratchpad`, `internal_monologue`, `thoughts` all get stripped.
_BANNED_SUBSTRINGS: tuple[str, ...] = (
    "thinking",
    "thought",
    "chain_of_thought",
    "chain-of-thought",
    "reasoning",
    "scratchpad",
    "monologue",
    "cot",
)


def _now_iso() -> str:
    """UTC timestamp, second precision, trailing 'Z' (matches §11.2 example)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_banned_key(key: str) -> bool:
    lowered = key.lower()
    return any(bad in lowered for bad in _BANNED_SUBSTRINGS)


def _strip_reasoning(value: Any) -> Any:
    """Recursively drop any mapping key that looks like a reasoning transcript.

    Over-rejecting here is the safe direction (AGENTS.md): a dropped field is
    visible as an absence; a leaked chain-of-thought corrupts the evidence
    silently and violates Blueprint §29.
    """
    if isinstance(value, dict):
        return {
            k: _strip_reasoning(v)
            for k, v in value.items()
            if not (isinstance(k, str) and _is_banned_key(k))
        }
    if isinstance(value, (list, tuple)):
        return [_strip_reasoning(v) for v in value]
    return value


class Trajectory:
    """Append-only JSONL event log.

    Not thread-safe by design: one `Trajectory` follows one analysis run, and
    stages within a run execute sequentially. Every `emit` opens the file in
    append mode, writes exactly one line, and closes it, so a crash leaves a
    valid prefix rather than a truncated final record.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._step = 0
        # Count api_call events per requirement so the §11.3 cache alarm can
        # fire on the *second* requirement of a run.
        self._api_calls_seen = 0

    # ── low-level append ────────────────────────────────────────────────

    def emit(
        self,
        event_type: EventType,
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Write one event line and return the record that was written.

        `event_type` must be one of the six known types. Unknown caller fields
        are permitted (the API is forward-compatible) but scrubbed of any
        reasoning-transcript keys first.
        """
        if event_type not in _EVENT_TYPES:
            raise ValueError(
                f"unknown event type {event_type!r}; "
                f"must be one of {sorted(_EVENT_TYPES)}"
            )

        self._step += 1
        record: dict[str, Any] = {
            "step": self._step,
            "ts": _now_iso(),
            "type": event_type,
        }
        if requirement_id is not None:
            record["requirement_id"] = requirement_id
        if stage is not None:
            record["stage"] = stage

        # Scrub caller-supplied payload before it can reach disk.
        record.update(_strip_reasoning(fields))

        line = json.dumps(record, ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return record

    # ── typed helpers ───────────────────────────────────────────────────

    def tool_call(
        self,
        tool: str,
        input: dict[str, Any] | None = None,
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "tool_call",
            requirement_id=requirement_id,
            stage=stage,
            tool=tool,
            input=input or {},
            **extra,
        )

    def tool_result(
        self,
        tool: str,
        result_summary: str,
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "tool_result",
            requirement_id=requirement_id,
            stage=stage,
            tool=tool,
            result_summary=result_summary,
            **extra,
        )

    def decision(
        self,
        rationale: str,
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """A concise, model-authored one-liner (§11.1) — never a transcript."""
        return self.emit(
            "decision",
            requirement_id=requirement_id,
            stage=stage,
            rationale=rationale,
            **extra,
        )

    def api_call(
        self,
        model: str,
        request_id: str | None,
        usage: Any,
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Record a model call: `request_id` and the *full* usage object.

        Cost is summed from these records (§11.2), so the usage object is
        preserved verbatim. Fires the §11.3 cache-miss alarm on the second
        api_call of a run when `cache_read_input_tokens == 0`.
        """
        usage_dict = _coerce_usage(usage)
        record = self.emit(
            "api_call",
            requirement_id=requirement_id,
            stage=stage,
            model=model,
            request_id=request_id,
            usage=usage_dict,
            **extra,
        )

        self._api_calls_seen += 1
        # §11.3: the alarm is meaningful from the second call onward — the very
        # first call legitimately has nothing cached to read.
        if self._api_calls_seen >= 2 and _cache_read(usage_dict) == 0:
            self.error(
                "cache_miss",
                "cached prefix was invalidated — check for interpolation "
                "into the system prompt",
                requirement_id=requirement_id,
                stage=stage,
            )
        return record

    def verdict(
        self,
        payload: dict[str, Any],
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "verdict",
            requirement_id=requirement_id,
            stage=stage,
            payload=payload,
            **extra,
        )

    def error(
        self,
        kind: str,
        message: str,
        *,
        requirement_id: str | None = None,
        stage: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return self.emit(
            "error",
            requirement_id=requirement_id,
            stage=stage,
            kind=kind,
            message=message,
            **extra,
        )

    # ── read-back ───────────────────────────────────────────────────────

    def read(self) -> list[dict[str, Any]]:
        """Read every event back, in written order."""
        return list(read_events(self.path))


def _coerce_usage(usage: Any) -> dict[str, Any]:
    """Normalise an anthropic usage object (or a dict) into a plain dict.

    Track C must never `import anthropic`, so we duck-type: prefer a
    `model_dump()` / `to_dict()` method, fall back to `__dict__`, accept a
    plain dict as-is. The result is scrubbed like any other payload.
    """
    if usage is None:
        return {}
    if isinstance(usage, dict):
        data = usage
    elif hasattr(usage, "model_dump"):
        data = usage.model_dump()
    elif hasattr(usage, "to_dict"):
        data = usage.to_dict()
    elif hasattr(usage, "__dict__"):
        data = {k: v for k, v in vars(usage).items() if not k.startswith("_")}
    else:
        data = {"value": usage}
    return _strip_reasoning(data)


def _cache_read(usage: dict[str, Any]) -> int:
    """Extract cache_read_input_tokens, treating missing/None as 0."""
    value = usage.get("cache_read_input_tokens")
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def read_events(path: str | os.PathLike[str]) -> Iterator[dict[str, Any]]:
    """Yield each JSONL record in order. Blank lines are skipped."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
