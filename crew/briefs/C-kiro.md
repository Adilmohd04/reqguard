# Brief C — Evidence Infrastructure

**Tool:** kiro_cli (CAO agent `e1018844`, role: code_supervisor - dev hat)
**Branch:** `track/c-evidence`
**Worktree:** `../reqguard-C`
**Tasks:** `C1, C5, C6` (see `crew/tasks.yaml`)
**Note:** the benchmark corpus (`C2`–`C4`) moved to Track E (antigravity).

---

## Why you have this track

ReqGuard's claim is empirical. Your track is the evidence: 18 benchmark cases with
known ground truth, a deterministic evaluator, and the report that carries the
numbers. If the agent works perfectly and your layer is wrong, there is nothing to
show. If the agent is mediocre and your layer is right, we still have a real result
and an honest submission.

You also build 15 near-identical benchmark cases from one template — spec-driven
generation from a fixed schema, which is what Kiro is for.

---

## Your files — and only these

```
src/reqguard/evaluation/metrics.py         the evaluator
src/reqguard/evaluation/benchmark.py       the runner harness
src/reqguard/reporting/**                  terminal, JSON, summary.html
src/reqguard/tracing/trajectory.py         the JSONL logger
tests/unit/test_metrics.py, test_ground_truth.py, test_trajectory.py
```

Full list: `crew/ownership.yaml` → `owned_by_C`.

## Hard rules

1. **Never `import anthropic`.** The evaluator must be re-runnable against stored
   artifacts — re-scoring must never cost money or vary between runs. The gate greps.
2. **Never edit `models.py` or `errors.py`.** Frozen. Contract change request only.
3. **Never edit files outside your ownership list.** The gate checks.
4. Read `docs/03-implementation-manual.md` §§11, 12, 15, 17.

---

## Start with case01 and stop

Task `C2` builds **one** case, complete, from manual §15.2. Do not start `C3` until
`C2`'s gate is green:

```bash
cd benchmarks/case01-cancelled-order-shipping && pytest -q     # must be: 2 passed
```

Case 01 is the template for fourteen more. A flaw you clone fourteen times is
fourteen bad cases; a flaw you fix once is fixed everywhere.

The defining property of case01 is **an absence**: nothing in its test suite
constructs a cancelled order. That gap is the case. It is easy to "helpfully" add a
test that covers it — doing so destroys the case.

---

## The three things that make the numbers trustworthy

### 1. `benchmarks/validate.py` — task C4

Runs in CI, makes **no API calls**. For every case:

- the suite is green on unmutated code (a red suite voids the case — manual §8.3)
- `ground_truth.json` parses; every requirement has an explicit `protected` label
- every `protected: false` has a stated `rationale`
- clean cases contain no deliberate defect
- case ids are unique and match directory names

A silently broken case produces a plausible-looking wrong number. This script is
cheap insurance against exactly that.

### 2. Three clean cases, not two — task C3

Doc 2 §23 says "at least 2". With 2, the measurable false-positive rate can only be
0%, 50%, or 100% — it cannot distinguish *one slip* from *systematically cries wolf*,
which is the distinction the metric exists to measure. Three gives 0/33/67/100.
Still coarse; meaningful.

### 3. The evaluator contains no model — task C5

Two metric families, both computed from the same artifacts, neither chosen after
seeing the numbers:

| Family | Arms | Why |
|---|---|---|
| Gap-detection recall / precision / clean-case FP | **both** | the same yes/no question per requirement — comparable |
| Mutation kill rate, repair success rate | **ReqGuard only** | the baseline structurally cannot generate mutations |

Also required:
- inconclusive rate **reported, never buried**
- cost summed from trajectory `usage` records, never estimated from a price table
- re-assert, from the recorded diffs, that no mutation touched a test file
- record a SHA-256 of the `benchmarks/` tree into `metrics.json` — Doc 2 §25 forbids
  changing cases after seeing results, and this makes a violation visible

---

## Trajectory logger — task C1, and Tracks A and B both call it

Append-only JSONL. Event types: `tool_call`, `tool_result`, `decision`, `api_call`,
`verdict`, `error`.

**Doc 2 §29 is a hard constraint:** log observable actions and concise rationales.
Never write raw thinking or chain-of-thought. If a caller hands you a field that
looks like a reasoning transcript, drop it — the API is yours to define, so define
it so the wrong thing cannot be logged.

`api_call` events carry `request_id` and the full `usage` object. Emit a
`cache_miss` error event if `cache_read_input_tokens` is 0 on the second requirement
of a run — a silently broken cache is a 5× cost regression that otherwise surfaces
on the invoice.

---

## `summary.html` — task C6

Self-contained: inline CSS, zero external assets. It must open from disk and survive
being emailed.

It is a **generated report**, not a web application. Doc 1 §5 is explicit that
ReqGuard is not a web app; this file does not change that, and it should not grow
into one.

Repairs are written to the report directory, **never** to the target repo.

---

## Definition of done, per task

The `gate` in `crew/tasks.yaml` exits 0 from a clean worktree. Then push and report
the task id. The Supervisor re-runs the gate on their side.

Your track depends on nothing from A or B. You can go from `C1` to `C6` without
waiting for anyone — which means if either other track stalls, evidence work
continues.
