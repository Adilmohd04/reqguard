# Improvement Changelog

ReqGuard is an experiment, not a feature list. Each version below is a
hypothesis about what actually improves requirement protection, and each is kept
or discarded on evidence — not on whether it sounded clever. The through-line is
Doc 1 §28: *if a component does not measurably help, it is removed, and the
removal is itself the finding.*

Every number that appears here — and every number that ever will — is produced by
`reqguard evaluate` from committed artifacts (§17.1, the Numbers Rule). Where a
metric has not yet been produced by a real scored run, it is written as
`[pending: reqguard evaluate]` rather than invented. The methodology is frozen
regardless of whether the paid run has happened yet.

---

## How each step is measured

The primary metric is comparable across both arms, because the baseline can
produce it too:

- **gap-detection recall** — of the 15 defect requirements, how many did the arm
  flag as an actual gap (`INSUFFICIENTLY_PROTECTED` or `REPAIRED`)?
- **gap-detection precision** — of everything the arm flagged, how much was a
  real gap rather than a false alarm?
- **clean-case false-positive rate** — of the 3 clean requirements
  (case05, case11, case17), how many did the arm wrongly flag?

Two secondary metrics apply to ReqGuard only, because the baseline structurally
cannot produce them — it never mutates code or writes a test:

- **mutation kill rate** — did the generated mutation actually change behavior the
  suite should have caught?
- **repair success rate** — of surviving mutations, how many did a generated test
  then kill while still passing on the original?

The evidence layer for all of this is the 18-case corpus and its replay fixtures,
which let the entire pipeline — and the scoring — run offline, deterministically,
for free.

---

## v0 — General LLM review (baseline established)

**Hypothesis:** a capable model, given the repo and the one question a developer
would actually type — *"does the suite cover this requirement?"* — is already a
strong reviewer.

This is the control arm (`reqguard benchmark --arm baseline`). It runs the **same
model at the same effort (`xhigh`) with the same read-only tools**. Handicapping
it would invalidate the whole experiment (AGENTS.md, load-bearing rule 3), so we
deliberately do not. The baseline reads code and reasons; it never executes a
mutation or a test.

**What it establishes:** the ceiling reachable by reasoning alone. Everything
after v0 has to beat *this*, not a strawman.

- Result: `[pending: reqguard evaluate]` recall / precision / clean-FP.

## v1 — Requirement extraction (improvement measured)

**Hypothesis:** naming the requirement as a structured `Behavior`
(`precondition → prohibited_action → expected_result`) before looking at code
stops the reviewer from grading the code that exists instead of the requirement
that matters.

**Change:** the requirement analyzer emits one `Requirement` per rule with a
verbatim `statement` and a machine-checkable behavior triple.

**Why it should help:** it converts a vague "review this" into a specific,
falsifiable claim about one behavior — the thing a later stage can actually try
to violate.

- Result: `[pending: reqguard evaluate]` recall vs. v0.

## v2 — Adversarial mutations (improvement measured)

**Hypothesis:** the only trustworthy evidence that a suite protects a requirement
is that the suite *fails* when the requirement is violated. So violate it.

**Change:** for each requirement, derive a minimal, plausible code change that
breaks exactly that requirement, apply it in a sandbox, and run the suite. A
mutation the suite does not catch (`SURVIVED`) is a concrete, reproducible gap —
not an opinion.

**Why it should help:** it replaces "I think this is covered" with "here is the
one-line change that breaks it and the tests still pass." The killer case
(case13) is the sharpest example: `80/100 → completed` survives a suite that only
ever tested `0` and `100`.

- Result: this is where ReqGuard gains the ability to produce a **kill rate** at
  all — the baseline cannot. `[pending: reqguard evaluate]`.

## v3 — Semantic mutation planning (improvement measured)

**Hypothesis:** a fixed catalogue of syntactic mutations (flip a comparison, drop
a guard) misses violations that are about *meaning*, not syntax.

**Change:** the mutation planner picks from the constrained catalogue
(`remove_guard`, `comparison_change`, `boolean_inversion`, `boundary_shift`,
`allow_transition`, `remove_validation`) **and** a `semantic_change` escape hatch
that requires a written rationale. case13 (partial fulfillment) and case18
(banker's rounding → half-up) are protected by exactly this: no catalogue entry
names "reports a partial order as completed" or "biases tax rounding upward."

**Why it should help:** the hardest, most valuable gaps are semantic. Without this
step they are simply invisible.

- Result: `[pending: reqguard evaluate]` — recall on the two `hard`/`semantic`
  cases specifically.

## v4 — Test repair (improvement measured)

**Hypothesis:** finding a gap is worth more if the tool also closes it.

**Change:** when a mutation survives, the repairer writes a test targeting the
uncovered behavior. The verdict becomes `REPAIRED` only after verification (v5)
confirms the test is real.

**Why it should help:** it turns a report into a pull request. This is the only
stage that ever proposes a write, and it writes to the report directory —
`reqguard apply` is the sole path into the user's repo, and it refuses unverified
repairs.

- Result: **repair success rate** across the 15 defect cases —
  `[pending: reqguard evaluate]`. In the offline corpus, all 15 defect fixtures
  carry a repair that passes on the original and fails on the mutant (verified by
  `reqguard doctor`, step 6).

## v5 — Independent verification (improvement measured)

**Hypothesis:** a generated test cannot be trusted on the strength of the model
saying it works. It has to be executed, twice, against a known-good and a
known-bad tree.

**Change:** a repair is `verified` only if **both** conditions hold —
`passes_on_original` (condition A) **and** `kills_mutant` (condition B). Either one
alone is a trap: `assert False` kills every mutant and protects nothing;
a test that only passes on the original detects nothing.

**Why it should help:** it is the difference between "the model claims this test
helps" and "this test demonstrably fails the moment the requirement is violated."

- Result: verification is what makes the repair rate *earned* rather than
  self-reported. `[pending: reqguard evaluate]`.

## v6 — Remove unnecessary component (no measurable regression)

**Hypothesis (the honest one):** not everything we built earns its place. The
discipline is to measure that and cut what doesn't.

**Method:** ablate a component, re-score against the committed corpus with
`reqguard evaluate`, and keep the removal if recall / precision / clean-FP do not
regress. Because scoring is deterministic and free, an ablation costs nothing to
verify — the replay corpus makes "does this component matter?" a re-runnable
experiment rather than an argument.

- Candidate + result: `[pending: reqguard evaluate ablation run]`. The point of
  recording it here first is that the removal is reported as evidence of judgment,
  not hidden as a quiet deletion.

---

## Infrastructure that makes the above honest

These are not versions of the *method*; they are what keeps the measurements
trustworthy.

### Offline replay corpus (all 18 cases)

The whole pipeline — analyze → investigate → mutate → execute → repair → verify —
plus the baseline arm, runs from committed JSON fixtures under
`benchmarks/replay/`. No API key, no spend, byte-for-byte reproducible. This is
the CI path and the demo path.

Every one of the 18 cases passes `reqguard doctor --replay` end-to-end (exit 0,
all six kill criteria green):

- **15 defect cases** produce `SURVIVED` then a verified `REPAIRED`, matching
  their `protected: false` ground truth.
- **3 clean cases** (case05 API-token-scope, case11 max-refund-limit,
  case17 missing-auth-header) produce `KILLED` — the mutation is caught by the
  existing suite, so ReqGuard correctly does **not** flag them. This is the
  false-positive guard: a tool that flags everything is useless.
- The **killer case** (case13, partial fulfillment) carries `baseline: PROTECTED`
  by design (Doc 1 §29) — the contrast between a reasoning-only pass and an
  execution-driven `SURVIVED` is the experiment in a single case.

The corpus exercises all seven mutation types: `remove_guard` (cases 02, 03, 04,
05, 09, 15, 17), `allow_transition` (06, 07, 08), `comparison_change` (10, 11),
`boundary_shift` (14, 16), `remove_validation` (12), and `semantic_change`
(13, 18).

### Platform hardening

The test runner and CLI were hardened for Windows/POSIX parity: POSIX-mode
`shlex.split` mangling Windows paths, a bare `pytest` command depending on an
activated venv, `NotADirectoryError` vs `FileNotFoundError` for a missing sandbox,
and cp1252-vs-utf-8 encoding on non-ASCII fixture content. All four were real,
platform-specific, and would have silently scored an `ENVIRONMENT_ERROR` as a
mutation kill — inflating the kill rate for free, exactly the failure mode
AGENTS.md's load-bearing rule 2 exists to prevent.

---

## The claim this changelog supports

ReqGuard does not claim to have invented mutation testing or AI test generation.
The narrower, defensible claim is:

> ReqGuard connects natural-language requirements to adversarial,
> requirement-specific mutations and uses executable test outcomes to decide
> whether the requirement is actually protected — and it measures that decision
> against a baseline using the same model at the same effort.

Each version above is a step toward that claim, kept or cut on measured evidence.
The placeholders remain placeholders until a real scored run replaces them.
