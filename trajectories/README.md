# Agent trajectories

Two kinds of trajectory exist for this submission — ReqGuard's own internal
agent (the product being evaluated), and the coding agents used to build it.

## 1. ReqGuard's own agent — two representative runs

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

## 2. The coding agents used to build ReqGuard

`../COLLAB.md`, in full, is the trajectory for this half: an append-only,
timestamped log of every session by both coding agents used on this project
(Claude Code and Kiro), each entry stating what was tried, what the evidence
showed, what was decided, and what was explicitly flagged rather than
silently done. It includes the real retries and pivots this deliverable asks
for — e.g. the three provider migrations (Anthropic → Gemini → OpenRouter →
Conduit), each one forced by a concrete blocker (free-tier quota, credit
wall) and each verified empirically before being trusted.
