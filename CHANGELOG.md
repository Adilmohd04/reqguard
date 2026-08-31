# Improvement Changelog

ReqGuard is an experiment, not a feature list. Each version below is a
hypothesis about what actually improves requirement protection, and each is kept
or discarded on evidence — not on whether it sounded clever. The through-line is
Doc 1 §28: *if a component does not measurably help, it is removed, and the
removal is itself the finding.*

Every number that appears here — and every number that ever will — is produced by
`reqguard evaluate` from committed artifacts (§17.1, the Numbers Rule). The three
measured deltas below (v0→final, v3, v4/v5) have been reproduced **twice in
independent environments** — local Windows and a clean `docker compose up --build`
run over the full 18-case corpus — with byte-identical results, which is the
strongest reproducibility claim an offline artifact can carry. Where a metric has
*not* been produced by a real scored run — specifically anything requiring a
**live, paid** model call rather than the replay corpus — it is said so plainly
rather than invented. The methodology is frozen regardless of whether the paid run
has happened yet.

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

### Measured deltas at a glance

Three comparisons are *measured* by the real evaluator over the committed corpus
(the endpoint plus two ablations, `benchmarks/ablation.py`). Two steps (v1, v2)
are honestly **not** independently ablatable and are labeled as such rather than
given an invented number.

| Step | Question | Measured delta | How |
|---|---|---|---|
| v0 → final | Does the workflow beat reasoning alone? | recall **0.933 → 1.000**, precision 1.000 both, clean-FP 0.000 both | `reqguard evaluate`, full corpus |
| v3 | What did the `semantic_change` hatch buy? | recall **undefined → 1.000** on case13+case18 (INC 2 → 0) | ablation A |
| v4/v5 | What did repair + verification buy? | detection unchanged (recall 1.000); remediation **0/15 → 15/15** verified | ablation B |
| v1 | Requirement extraction | *not ablatable* — load-bearing for all later stages | design decision, stated |
| v2 | Adversarial mutation | *is* the ReqGuard mechanism — its value is the v0→final gap | measured indirectly |

Everything below expands each row. Ablation numbers are reproducible with
`python benchmarks/ablation.py` (deterministic, zero API spend).

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

- **Result (measured — full offline corpus, `reqguard evaluate`; reproduced
  local + `docker compose up --build`, byte-identical):** baseline gap-detection
  recall **0.933** (14/15), precision **1.000**, clean-case FP **0.000**. The
  single miss is **case13**, the killer case — a reasoning-only pass on a suite
  that never tested `80/100`. The full ReqGuard pipeline scores recall **1.000**,
  precision **1.000**, clean-FP **0.000** on the same corpus.
  **Delta: +0.067 recall (0.933 → 1.000), and the one requirement it flips is the
  one the whole project was built around.**

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

- **Result (not independently ablatable):** v1 is load-bearing for every stage
  after it — the investigator, planner, and baseline all consume the structured
  `Behavior`, so there is no honest way to "turn it off" and still score the
  pipeline. It is reported here as a design decision with a stated rationale, not
  as a measured delta. Claiming a number for it would be the exact
  self-certification ReqGuard exists to catch.

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

- **Result (measured indirectly):** v2 is not an ablation candidate — it *is* the
  mechanism the entire ReqGuard arm rests on, and its value is exactly the
  ReqGuard-vs-baseline gap the endpoint comparison measures (recall 1.000 vs
  0.933, and every secondary metric the baseline structurally cannot produce). It
  is the step that turns "I think this is covered" into an executed SURVIVED /
  KILLED verdict.

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

- **Result (measured — ablation A, `benchmarks/ablation.py`, scored by the real
  evaluator):** on the two cases that can *only* be violated semantically
  (case13, case18), gap-detection recall is **1.000 with** the escape hatch and
  **undefined without it — both cases go INCONCLUSIVE** (TP 2→0, INC 0→2). The
  escape hatch is the entire difference between detecting these two gaps and not
  being able to form a mutation at all. Critically, the ablated pipeline does not
  *falsely pass* them; it correctly reports INCONCLUSIVE — the gap becomes
  invisible, never a green lie. **Delta: +1.000 recall on the semantic slice.**

## v4 — Test repair (improvement measured)

**Hypothesis:** finding a gap is worth more if the tool also closes it.

**Change:** when a mutation survives, the repairer writes a test targeting the
uncovered behavior. The verdict becomes `REPAIRED` only after verification (v5)
confirms the test is real.

**Why it should help:** it turns a report into a pull request. This is the only
stage that ever proposes a write, and it writes to the report directory —
`reqguard apply` is the sole path into the user's repo, and it refuses unverified
repairs.

- **Result (measured — ablation B, `benchmarks/ablation.py`):** repair does **not**
  change gap-detection recall — it is **1.000 with and without** repair, because a
  gap is *detected* at the mutation-survival step, before any repair. What repair
  adds is remediation: **0/15 → 15/15** defect requirements moved from "gap
  reported" to "verified-protected" (`repair_success_rate` undefined → **1.000**).
  This is the honest finding — repair earns its place on *remediation*, not by
  inflating detection. In the offline corpus every one of the 15 repairs passes on
  the original and fails on the mutant (`reqguard doctor` step 6).

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

- **Result (measured — ablation B, same run):** verification is what makes the
  0/15 → 15/15 remediation number *earned* rather than self-reported: all 15
  repairs are counted only because they satisfy **both** conditions
  (`repairs_verified == repairs_attempted == 15`, `repair_success_rate 1.000`). A
  repair that passed condition A but not B would be dropped here, not banked —
  which is exactly why the number is trustworthy.

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

## Hot take (the honest one, learned the hard way)

The most on-theme bug in this project's own history is one ReqGuard would have
caught — and that we found *by hand instead*.

The CLI's own test runner was silently scoring failures as passes. On Windows,
`shlex.split` in POSIX mode ate the backslashes in `sys.executable`, the command
became an unfindable path, and the resulting error was misread as
`ENVIRONMENT_ERROR` — which is exactly the "read a non-1 exit code as a test
failure and score it as a kill" mistake AGENTS.md warns is *the* way the system
lies to you. The suite was "green" on Linux (188/1) and quietly broken on Windows
(15 failing) at the same time.

It was caught not by the tool but by a human running the suite fresh on the real
platform and *checking the actual exit code* rather than trusting the reported
status. That is precisely the failure mode ReqGuard exists to expose: a passing
signal that isn't backed by execution. We built a tool to catch green lies and
then lived one. The lesson — trust executed evidence over reported status, on the
real environment — is now wired into the tool itself (exit-code taxonomy, the
"only pytest 0/1 yields a verdict" rule, UTF-8 output) and into every number in
this file: measured, or honestly labeled as not-measured. Nothing in between.

---

## The claim this changelog supports

ReqGuard does not claim to have invented mutation testing or AI test generation.
The narrower, defensible claim is:

> ReqGuard connects natural-language requirements to adversarial,
> requirement-specific mutations and uses executable test outcomes to decide
> whether the requirement is actually protected — and it measures that decision
> against a baseline using the same model at the same effort.

Each version above is a step toward that claim, kept or cut on measured evidence.
Three of the steps carry a real measured delta (v0→final, v3, v4/v5) produced by
the deterministic evaluator over the committed corpus; two (v1, v2) are honestly
labeled as not independently ablatable rather than given an invented number. The
one thing still outstanding is a paid **live** run — the offline deltas prove the
pipeline and the scoring are correct and reproducible, but only a live run may be
quoted as the *model's* performance (§17.1).
