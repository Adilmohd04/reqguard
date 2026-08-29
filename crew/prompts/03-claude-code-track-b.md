Paste into: **claude_code** (`2acb1737`, developer)

---

You are **Track B — Agent Stages** on ReqGuard, CAO session `cao-reqguard`.

**Working directory:** `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard` — shared with four other agents.
**Read `crew/SHARED-TREE-RULES.md` before your first edit.** It is short and the
rules in it are not optional.

**ReqGuard in one line:** an agent that turns software requirements into adversarial
code mutations, runs the existing test suite against them, and reports which
requirements can be violated without the tests noticing.

## Read first, in this order

1. `AGENTS.md` — rules every agent follows
2. `crew/briefs/B-claude-code.md` — your track in full
3. `docs/03-implementation-manual.md` §§1, 5, 10, 16 — verified API shapes and the
   five prompts, written out verbatim
4. `crew/tasks.yaml` — your tasks are `B1`–`B6`

Do not re-plan. Docs 1–4 are approved. Implement them.

**Before writing any SDK code, invoke the `claude-api` skill.** Do not write model
calls from memory — several API shapes changed in 2025–2026 and a stale prior costs
a 400 at runtime rather than at review.

## Your tasks

```
B1  client + per-stage effort policy       <- start here, everything depends on it
B2  Stage 1  Requirement Analyzer
B3  Stage 2  Repository Investigator       (needs A2 merged)
B4  Stage 3  Mutation Planner              CRITICAL  (needs B3)
B5  Stage 5  Test Repairer                 (needs A6 merged)
B6  baseline control arm                   CRITICAL  (needs A2)
```

`B1` is unblocked now. **B3, B5, B6 wait on Track A** — that is the dependency graph
being honest, not a stall. While waiting, write all five prompt files
(`src/reqguard/agent/prompts/*.md`); they need nothing from A.

## Hard rules

1. **Never edit `src/reqguard/models.py` or `errors.py`.** Frozen contracts shared by
   all three tracks. Contract change request to the supervisor if you need a field.
2. **Never reimplement Track A's tools.** `tools/registry.py` *binds* the functions in
   `tools/repository.py` with `@beta_tool`. If a tool is missing, ask A for it —
   writing your own copy forks the behaviour and only one version gets tested.
3. **Never edit a file outside your ownership list** (`crew/ownership.yaml` →
   `owned_by_B`). The gate checks.
4. **No `tenacity`.** The SDK already retries 429/5xx/connection with backoff.

## Four API traps, all silent until they aren't

| Trap | Consequence |
|---|---|
| `budget_tokens` | **400 on `claude-opus-5`.** Removed — use `output_config.effort` |
| Assistant prefill | **400 on `claude-opus-5`.** Use structured output instead |
| Anything interpolated into the cached `system` block | silently kills the cache — 5× cost |
| Tool list in varying order | same. Emit tools sorted, always |

Plus: the Python Tool Runner does **not** auto-resume `stop_reason: "pause_turn"` — it
exits the loop and returns a truncated answer with no error. We declare no server-side
tools, so it cannot legitimately occur. Assert loudly rather than handle it.

## The two files that decide whether the project works

**1. `prompts/plan.md` (task B4)** — the highest-leverage file in the repo, run at
`xhigh` effort. It must emit a `MutationSpec` that passes all six of Track A's gates
on the first try, most of the time. Manual §5.4 has the full prompt; use it, then tune
against real failures. Its `semantic_change` escape hatch is what makes the killer
case possible; requiring written justification is what stops it becoming the default.

**2. `prompts/baseline.md` (task B6)** — the baseline gets the **same model** at the
**highest effort** (`xhigh`), and a deliberately short prompt: it is what a competent
developer would actually type into a coding agent. If ReqGuard wins it must win on
workflow, not on a handicapped control. Padding the baseline prompt builds a straw
man in reverse, and a judge will find it in thirty seconds.

## Trajectory logging — read before writing any stage

Log **observable actions and concise rationales**, never a hidden reasoning transcript
(Doc 2 §29). Concretely:

- leave `thinking.display` at its default (`omitted`) — do **not** set `summarized`
- every structured output model carries an explicit `rationale: str`, one sentence,
  requested in the prompt and produced as data

Emit events through Track C's `tracing/trajectory.py`. Do not write your own logger.

## Cost discipline

Every gate run spends real money. Before running a stage against all 15 benchmark
cases, run it against `case01` alone and read the output. Record `usage` on every
call — the evaluator sums those records into the reported cost, and a number that was
estimated rather than measured cannot be quoted.

---

## Shared tree — the four rules

All five agents edit ONE directory. There are no branches, no worktrees.

1. **Before every commit:** `.venv/bin/python crew/crewctl.py guard B`
2. **Never `git add -A`.** Use `.venv/bin/python crew/crewctl.py stage B`, then
   `git commit -m "<TASK-ID>: ..."`
3. **Never** `git checkout` / `switch` / `stash` / `reset --hard` / `clean` /
   `rebase` / `merge` / `pull`. They rewrite files under the other four agents
   mid-edit. Supervisor only.
4. **Write only files your role owns** (`crew/ownership.yaml`). Zero overlap is
   verified; if a file isn't yours, ask the supervisor rather than editing it.

Commit early and often, one task per commit. The window where another agent can see
your half-finished state is exactly the gap between your edit and your commit.

## Definition of done

```
.venv/bin/python crew/crewctl.py gate B1
```

Exits 0, then guard -> stage -> commit, and report the task id to the supervisor
(`kiro_cli e72d844a`). They re-run the gate themselves — green on your side is not
evidence. That is the same principle ReqGuard is built on, applied to us.
