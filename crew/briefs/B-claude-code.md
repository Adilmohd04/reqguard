# Brief B — Agent Stages

**Tool:** claude_code (CAO agent `cd5122b0`, role: developer)
**Branch:** `track/b-agent`
**Worktree:** `../reqguard-B`
**Tasks:** `B1 → B2, B3, B4, B5, B6` (see `crew/tasks.yaml`)

---

## Why you have this track

You own **every Anthropic SDK call in the repository**. One track owns the model
surface, so when an SDK shape changes exactly one worktree needs fixing — and the
other two keep running in CI with no key.

Before writing any SDK code, invoke the `claude-api` skill. Do not write model calls
from memory: several shapes changed in 2025–2026 and a stale prior costs a 400 at
runtime, not at review.

---

## Your files — and only these

```
src/reqguard/agent/client.py                the shared call shape
src/reqguard/agent/policy.py                per-stage effort table
src/reqguard/agent/requirement_analyzer.py  Stage 1
src/reqguard/agent/repository_investigator.py Stage 2
src/reqguard/agent/mutation_planner.py      Stage 3
src/reqguard/agent/test_repairer.py         Stage 5
src/reqguard/agent/prompts/*.md             all five prompts
src/reqguard/tools/registry.py              @beta_tool bindings over Track A
src/reqguard/evaluation/baseline.py         the control arm
```

Full list: `crew/ownership.yaml` → `owned_by_B`.

## Hard rules

1. **Never edit `models.py` or `errors.py`.** Frozen. Contract change request only.
2. **Never reimplement Track A's tools.** `tools/registry.py` *binds* the functions
   in `tools/repository.py` with `@beta_tool`. If a tool is missing, ask A for it —
   writing your own copy forks the behaviour and only one of them gets tested.
3. **Never edit files outside your ownership list.** The gate checks.
4. Read `docs/03-implementation-manual.md` §§1, 5, 10, 16.

---

## Verified API shapes — §1 of the manual

These were checked against current SDK docs. Trust the manual over recall.

```python
client = anthropic.Anthropic()            # zero-arg; resolves env or ant profile

client.messages.parse(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},        # display: leave at default (omitted)
    output_config={"effort": "xhigh"},    # INSIDE output_config, not top-level
    system=[{"type": "text", "text": FROZEN,
             "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": volatile}],
    output_format=MutationSpec,           # returns .parsed_output, validated
)
```

**Four traps:**

| Trap | Consequence |
|---|---|
| `budget_tokens` | **400 on Opus 5.** Removed. Use `effort`. |
| Assistant prefill | **400 on Opus 5.** Use structured output instead. |
| Anything interpolated into the `system` block | silently kills the cache — 5× cost |
| Tool list in varying order | same — emit tools sorted, always |

**`pause_turn`:** the Python Tool Runner does not auto-resume it. It exits the loop
and returns a truncated answer with **no error**. We declare no server-side tools, so
it cannot legitimately occur — assert loudly rather than handle it:

```python
if last is not None and last.stop_reason == "pause_turn":
    raise ReqGuardInternalError("unexpected pause_turn — no server tools declared")
```

---

## The two places this track decides whether the project works

### 1. `prompts/plan.md` — task B4

The highest-leverage file in the repository, run at `xhigh` effort. It must produce a
`MutationSpec` that passes all six of Track A's gates on the first try, most of the
time. The full prompt is in manual §5.4 — use it, then tune against real failures.

Its `semantic_change` escape hatch is what makes the killer case (Doc 1 §29) possible.
Requiring a written justification is what keeps it from becoming the default.

### 2. `prompts/baseline.md` — task B6

**The baseline gets the same model and the highest effort setting (`xhigh`).**

If ReqGuard wins, it must win on workflow, not on a handicapped control. Keep the
prompt short — it is what a competent developer would actually type into a coding
agent. Padding it with ReqGuard's methodology builds a straw baseline in reverse,
and a judge will find it in thirty seconds.

---

## Trajectory logging — read this before writing any stage

Doc 2 §29: log **observable actions and concise rationales**, never a hidden
reasoning transcript.

Concretely, this means:

- leave `thinking.display` at its default (`omitted`) — do **not** set `summarized`
- every structured output model carries an explicit `rationale: str` — **one sentence**,
  requested in the prompt, produced as data

The rationale is a deliberate model-authored line, not a scraped internal monologue.
It is cheaper and more readable than summarized thinking.

Emit trajectory events through Track C's `tracing/trajectory.py`. Do not write your
own logger.

---

## Definition of done, per task

The `gate` in `crew/tasks.yaml` exits 0 from a clean worktree. Then push and report
the task id. The Supervisor re-runs the gate on their side.

**B3 and B4 need Track A's `A2` and `A3` merged first.** B1, B2, and all five prompt
files need nothing from A — write those while you wait. Being blocked here is the
dependency graph being honest, not a stall.

---

## Cost discipline

Every gate run spends real money. Before running a stage against all 15 benchmark
cases, run it against `case01` alone and read the output. Record `usage` on every
call — Track C's evaluator sums those records into the reported cost, and a number
that was estimated rather than measured cannot be quoted (manual §17.1).
