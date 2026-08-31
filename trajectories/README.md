# Agent trajectories

Three kinds of trajectory exist for this submission — ReqGuard's own
internal pipeline agents (the product being evaluated), the separate
baseline control-arm agent, and the coding agents used to build it.

## 0. The baseline arm — a distinct fifth agent

The baseline (`prompts/baseline.md`, `evaluation/baseline.py`) is not a
weaker version of the main pipeline — it's a genuinely different agent: one
LLM call, no mutation, no tools beyond read access, asked the question a
developer would actually type. Its representative trajectory is the replay
fixture the killer case is built on —
[`benchmarks/replay/case13-partial-fulfillment/REQ-13.baseline.json`](../benchmarks/replay/case13-partial-fulfillment/REQ-13.baseline.json):

```json
{
  "requirement_id": "REQ-13",
  "status": "PROTECTED",
  "reasoning": "The suite has two tests on fulfillment_status covering both
    documented outcomes: zero shipped reports 'pending' and all shipped
    reports 'completed'. The function is small and its three branches are
    exercised at their boundaries, so the reporting logic appears
    adequately covered.",
  "rationale": "Both status boundaries are tested, so the fulfillment
    reporting looks protected."
}
```

This is a real, confident, plausible-sounding verdict — and it's wrong. No
code was ever run to check it. This single artifact is the whole reason the
project exists: reasoning alone, however competent, isn't evidence.

## 1. ReqGuard's own pipeline agent — two representative runs

Both are `reqguard trajectory <path> --format md` renders of a real
`trajectory.jsonl` (also included raw, alongside each `.md`, as the
underlying source).

- **`replay-case01-full-arc.md`** — the complete intended arc, offline/replay,
  zero cost: investigate → plan → mutate → **survived** → repair →
  **verified**. Reproduce with:
  ```bash
  reqguard analyze benchmarks/case01-cancelled-order-shipping --replay benchmarks/replay
  reqguard trajectory benchmarks/case01-cancelled-order-shipping/reqguard-report/trajectory.jsonl --format md
  ```
- **`live-case13-gpt5-conduit.md`** — a **real, live, paid** run (`gpt-5` via
  Conduit) on the killer case (case13-partial-fulfillment): the agent
  investigates, correctly identifies the real enforcement point
  (`fulfillment_status`), applies a real `boundary_shift` mutation, gets a
  real `survived=True`, then writes a repair test and gets it **verified
  both ways** (`passes_on_original=True`, `kills_mutant=True`) — a complete
  live gap-detection-and-repair, 8 API calls, no replay involved. This is the
  second live attempt on this case; the first one (superseded, history in
  `COLLAB.md` sessions 7 and 10) crashed mid-repair on a real bug — an
  uncaught `PathEscape` from a malformed tool-call path that killed the whole
  attempt instead of letting the model see the error and retry. Fixed in
  `agent/client.py`'s tool-dispatch loop (any tool-execution exception now
  becomes a normal, recoverable tool result instead of a fatal crash) and
  re-verified live immediately after — this file is that second, successful
  run.

### The strongest evidence: an independent run against unmodified real code

**`real-world-pyjwt.md`** (+ `.jsonl`, `-repair-test.py`, `-requirements.md`)
is not part of ReqGuard's own benchmark corpus at all — it's a live run
against [PyJWT](https://github.com/jpadilla/pyjwt), cloned fresh, no
modification beyond a `requirements.md` written from PyJWT's own
`_validate_exp` code and RFC 7519 (the JWT spec's `exp` claim semantics).

**What this shows, precisely — no more:** ReqGuard mutated the expiry
boundary in `_validate_exp` (`exp <= now - leeway` → `exp < now - leeway`,
`boundary_shift`). PyJWT's own real test suite — 365 tests — passed 365/365
against the mutated code. **That boundary has no executable protection in
PyJWT's suite as it exists today.** This is not a claim that it's a bug, a
vulnerability, or that the gap was unknown to PyJWT's maintainers — the
inclusive comparison may be entirely deliberate. What's demonstrated is
narrower and fully evidenced: ReqGuard found and closed a real, executable
coverage gap in code it had never seen, without that code being built
around what the agent catches.

ReqGuard's agent then wrote and verified a real test for it
(`real-world-pyjwt-repair-test.py`) — passes on PyJWT's real code, fails on
the mutated version.

Nothing here was staged. This is the closest thing to "would this help a
real engineer, on real code they didn't write" the project has.

## 2. The coding agents used to build ReqGuard

`../COLLAB.md`, in full, is the trajectory for this half: an append-only,
timestamped log of every session by both coding agents used on this project
(Claude Code and Kiro), each entry stating what was tried, what the evidence
showed, what was decided, and what was explicitly flagged rather than
silently done. It includes the real retries and pivots this deliverable asks
for — e.g. the three provider migrations (Anthropic → Gemini → OpenRouter →
Conduit), each one forced by a concrete blocker (free-tier quota, credit
wall) and each verified empirically before being trusted.
