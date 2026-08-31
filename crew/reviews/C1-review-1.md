# C1 Review Round 1 — NEEDS_CHANGES

Commit reviewed: `0181dc9be2ae3c6be444feddfe5fa5c39979c927`

The supervisor gate passed (`17 passed`), but independent review found three blocking correctness defects.

## 1. Cache-miss semantics count calls, not requirements

`src/reqguard/tracing/trajectory.py` currently fires based on `_api_calls_seen >= 2`. Doc 3 §11.3 and task C1 require the alarm on the **second requirement of a run**, not the second API call. One requirement can make several model calls, so the current implementation can emit a false cache miss during REQ-01.

Fix by tracking distinct `requirement_id` values and evaluating the first API call for the second distinct requirement. Define and test behavior when `requirement_id` is absent. Add tests proving multiple calls for REQ-01 do not count as the second requirement, and a zero cache read on the first call for REQ-02 does.

## 2. Reasoning scrubber silently deletes legitimate fields

Bare substring matching for `cot` and `thought` drops legitimate domain keys such as `apricot_count`, `boycott_flag`, `mascot`, `cotton`, `thoughtful`, and `afterthought`. This corrupts trajectory evidence.

Use exact normalized keys or token/word-boundary matching for abbreviations and thought-related keys while still blocking actual transcript keys such as `cot`, `chain_of_thought`, `raw_thoughts`, `thinking`, `reasoning`, `scratchpad`, and `monologue`. Add regression tests showing legitimate keys survive recursively.

## 3. Payload fields overwrite reserved event metadata

`record.update(_strip_reasoning(fields))` runs after `step`, `ts`, and `type` are set, allowing callers to change the event type or destroy monotonic step/timestamp data.

Prevent collisions by rejecting/ignoring reserved payload keys or applying trusted metadata after sanitized fields. Add tests covering collisions with `step`, `ts`, and `type`.

## Required completion

Edit only C1-owned files. Run `.venv/bin/python crew/crewctl.py gate C1`, then role-C guard/stage and create a new `C1:` commit (do not amend). Return the new commit hash, changed absolute paths, and gate summary. The revision will receive another independent review.
