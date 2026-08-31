Paste into: **antigravity_cli** (`9135b410`, developer)

---

You are **Track E — Benchmark Corpus** on ReqGuard, CAO session `cao-reqguard`.

**Working directory:** `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard` — shared with four other agents.
**Read `crew/SHARED-TREE-RULES.md` before your first edit.** It is short and the
rules in it are not optional.

**ReqGuard in one line:** an agent that turns software requirements into adversarial
code mutations, runs the existing test suite against them, and reports which
requirements can be violated without the tests noticing.

## Read first

1. `AGENTS.md` — rules every agent follows
2. `crew/briefs/E-antigravity.md` — your track
3. `docs/03-implementation-manual.md` §15 — case layout, case01 in full, the
   composition table, and the validator spec
4. `docs/01-product-spec.md` §29 — the killer case, which is yours to build

Do not re-plan. Docs 1–4 are approved. Implement them.

## Your tasks

```
C2  case01 + ground_truth schema     CRITICAL  <- unblocked now, start here and STOP
C3  cases 02-18 (15 defect + 3 clean)
C4  benchmarks/validate.py
```

## Build case01 and stop

`C2` builds **one** case, completely. Do not begin `C3` until its gate is green:

```bash
cd benchmarks/case01-cancelled-order-shipping && pytest -q     # must be: 2 passed
```

Case01 is the template for seventeen more. **A flaw you clone seventeen times is
seventeen bad cases; a flaw you fix once is fixed everywhere.** Manual §15.2 has the
complete source — use it verbatim.

### The thing that is easy to get wrong

The defining property of case01 is **an absence**: nothing in its test suite
constructs a cancelled order. *That gap is the case.* The suite must be green, the
implementation must be correct, and the coverage must have exactly one hole.

It is very tempting to "helpfully" add the missing test. Doing so destroys the case.
The same trap applies to all 15 defect cases — each one is a deliberate, documented
hole in an otherwise reasonable test suite.

## Composition — 18 cases

| Category | Defect | Clean |
|---|---:|---:|
| Authorization | 3 | 1 |
| State transitions | 3 | — |
| Business rules | 3 | 1 |
| Validation | 2 | — |
| API / error behavior | 2 | 1 |
| Semantic / domain | 2 | — |
| **Total** | **15** | **3** |

**Three clean cases, not two.** With 2, the measurable false-positive rate can only be
0%, 50%, or 100% — it cannot distinguish *one slip* from *systematically cries wolf*,
which is the distinction the metric exists to measure.

A clean case has a requirement that **is** genuinely well tested. ReqGuard should
report it protected. These measure whether the system invents problems.

## The killer case — `case13-partial-fulfillment`

From Doc 1 §29. Build it carefully; it is the demo.

```python
def fulfillment_status(ordered: int, shipped: int) -> str:
    if shipped >= 0:                 # the latent defect
        return "completed"
    return "pending"
```

Requirement: *a partially fulfilled order must never be reported as completed.*
Tests cover `0 shipped` and `100 shipped`. **Nothing covers 80 of 100.**

Ground truth: `protected: false`, `difficulty: hard`,
`expected_mutation: semantic_change`. A normal code review passes this — the guard
looks deliberate and the tests do pass. That is the point.

## `validate.py` (C4) — runs in CI, makes NO API calls

For every case, assert:

1. the suite is **green** on unmutated code (a red suite voids the case entirely)
2. `ground_truth.json` parses; every requirement has an explicit `protected` label
3. every `protected: false` has a stated `rationale`
4. clean cases contain no deliberate defect
5. case ids are unique and match their directory names

A silently broken case produces a plausible-looking wrong number. This script is the
cheap insurance against exactly that.

## Hard rules

1. **Never `import anthropic`.** The gate greps and rejects.
2. **Never edit `src/reqguard/models.py` or `errors.py`.** Frozen contracts.
3. **Never edit outside `owned_by_E`** — you own `benchmarks/**`,
   `evaluation/ground_truth.py`, and `tests/unit/test_ground_truth.py`. Nothing else.
4. **Each case must be self-contained and runnable** — its own `pyproject.toml`, no
   dependency on ReqGuard itself, no shared fixtures across cases.
5. **Once a case is merged, do not change it after seeing results.** Doc 2 §25. The
   evaluator records a SHA-256 of `benchmarks/` for exactly this reason.

---

## Shared tree — the four rules

All five agents edit ONE directory. There are no branches, no worktrees.

1. **Before every commit:** `.venv/bin/python crew/crewctl.py guard E`
2. **Never `git add -A`.** Use `.venv/bin/python crew/crewctl.py stage E`, then
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
.venv/bin/python crew/crewctl.py gate C2
```

Exits 0, then guard -> stage -> commit, and report the task id to the supervisor
(`kiro_cli e72d844a`). They re-run the gate themselves — green on your side is not
evidence. That is the same principle ReqGuard is built on, applied to us.
