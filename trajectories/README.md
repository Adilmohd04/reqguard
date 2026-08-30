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
  Conduit) on the killer case (case13-partial-fulfillment). Not staged: the
  agent's investigate stage gets it wrong twice ("no code found"), retries
  after a cache-miss, then correctly identifies the real enforcement point
  (`fulfillment_status`'s `shipped >= ordered` guard), applies a real
  mutation, and gets a real `survived=True` on both requirements — a live gap
  actually detected. The run stops before a repair verdict (see
  `COLLAB.md`'s session 7 entry for the exact bug this exposed: a Windows
  path-resolution false-positive in the sandbox safety check, not a flaw in
  the agent's reasoning). Included precisely because it's honest, not
  because it's a clean success — the failure mode is real and instructive.

## 2. The coding agents used to build ReqGuard

`../COLLAB.md`, in full, is the trajectory for this half: an append-only,
timestamped log of every session by both coding agents used on this project
(Claude Code and Kiro), each entry stating what was tried, what the evidence
showed, what was decided, and what was explicitly flagged rather than
silently done. It includes the real retries and pivots this deliverable asks
for — e.g. the three provider migrations (Anthropic → Gemini → OpenRouter →
Conduit), each one forced by a concrete blocker (free-tier quota, credit
wall) and each verified empirically before being trusted.
