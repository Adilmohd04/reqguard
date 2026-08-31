# Recording guide — "ReqGuard on real libraries"

A speaker script for the demo video. Every fact below was verified this
session; nothing is invented. Where a result is partial or honest-negative,
the script says so — that reads as more credible, not less.

Two provider notes before you record:
- The **offline replay** demo needs no key and always works — use it for the
  live-on-camera commands so nothing can fail mid-recording.
- The **real-library** results (PyJWT, itsdangerous) were live runs; show them
  from the saved report/evidence files rather than re-running live on camera,
  because the model provider has a usage cap that may be spent at record time.

---

## 0:00–0:30 — The problem (say this)

"Every test suite here is green. That's the trap. Green means the tests you
*wrote* pass — it says nothing about the requirement you *meant*. The gap is
the case nobody thought to test. ReqGuard finds that gap by doing the one
thing a passing suite never does: it breaks the requirement on purpose and
checks whether the suite even notices."

**Show:** the README's one-liner:
`requirement → investigate → mutate → execute → repair → verify → verdict`

## 0:30–1:00 — What I intended

"My intent was simple to state and hard to satisfy: take a requirement in
plain English, and *prove with execution* — not opinion — whether the test
suite actually defends it. If it doesn't, don't just report it: write the
missing test, verify it fails on the broken code and passes on the correct
code, and hand it back. And it had to work on code the tool had never seen,
not just my own benchmark."

## 1:00–2:15 — What I got, on the benchmark (run live, offline, zero cost)

**Run:**
```
reqguard analyze benchmarks/case13-partial-fulfillment --replay benchmarks/replay
```
"Requirement: an order that's only partially shipped — 80 of 100 — must never
report 'completed'. The suite tests 0 shipped and 100 shipped. Both green.
ReqGuard mutates the partial branch to return 'completed', reruns the real
suite — still green. That's the gap the suite never covered."

**Then close it — the part that isn't just a report:**
```
reqguard apply benchmarks/case13-partial-fulfillment --requirement REQ-13 \
  --report-dir benchmarks/case13-partial-fulfillment/reqguard-report --yes --prove
```
"It writes the missing test into the repo, then runs the repo's *own* suite to
prove it: 3 passed, exit 0. The suite went from silently green over the gap to
genuinely protecting the requirement. If that suite hadn't passed, it rolls the
write back and leaves your repo untouched."

**On-screen result:** `✓ repo suite green with the repair: 3 passed` /
`✓ proven: REQ-13 is now protected`.

## 2:15–3:30 — The standout: it works on real libraries anyone uses

"The fair question about any benchmark is: did you build the cases around what
your tool catches? So I pointed it at real, widely-used libraries it had never
seen."

### PyJWT (show from `trajectories/real-world-pyjwt-evidence.md`)

"PyJWT — the library used to verify JSON Web Tokens, cloned unmodified at a
pinned commit. ReqGuard shifted the token-expiry check from `exp <= now` to
`exp < now` — one character. Then it ran PyJWT's own 365-test suite against the
change."

**Show the two lines from the evidence file:**
```
baseline: 365 passed          (exit 0)
mutated:  365 passed          (exit 0)   ← the mutation SURVIVES
```
"Same 365 pass. That expiry boundary has no executable protection in PyJWT's
suite. To be precise: this is *not* me claiming PyJWT has a bug — the inclusive
comparison may be deliberate. What's proven is narrower and real: a suite-passing
change can silently move that boundary, and ReqGuard found it in code I never
designed around, then wrote a test that pins it."

### itsdangerous (show honestly — this is the credibility beat)

"Second library: itsdangerous, the signing library Flask uses for sessions.
Requirement: a tampered signature must be rejected. ReqGuard found the exact
enforcing code — `verify_signature` and its caller `unsign` — and planned the
right mutation: invert the verification check so a bad signature passes."

"Here's the honest part. On that run the mutation was *rejected by ReqGuard's
own safety gate* — the model described the code block with slightly wrong
indentation, so it didn't byte-match the source, and the engine refused to
apply a mutation it couldn't anchor exactly. The result came back
INCONCLUSIVE — not a false pass. That's the system working as designed: when
it can't be sure, it says so instead of guessing."

"And that exposed a real robustness bug, which I fixed: ReqGuard now recovers
the true source block when it differs only in indentation, while still refusing
anything ambiguous. Three new tests cover it. That whole loop — a real repo
surfacing a real weakness, caught by the tool's own honesty rather than hidden
by it — is the point."

## 3:30–4:15 — The comparison (show from `reqguard evaluate`)

| Metric | ReqGuard | Baseline (ask-an-LLM) |
|---|---|---|
| Gap-detection recall | 1.00 | 0.933 |
| Precision | 1.00 | 1.00 |
| Clean-case false-positive rate | 0.0 | 0.0 |
| Repair success rate | 15/15 | n/a |

"Same model, same effort, same tools — the only difference is the workflow.
Across 18 cases the reasoning-only baseline misses exactly one: the partial-
fulfillment case. Neither ever falsely flags a clean suite."

## 4:15–4:45 — Anyone can run this on their own project

**Show:**
```
reqguard analyze .                 # your repo, your requirements.md
reqguard apply --requirement REQ-7 --prove
```
"Point it at your repo with a `requirements.md`, bring any OpenAI-compatible
key — it ships none. Offline replay and the whole test corpus run with no key
and no spend. It writes exactly one place: the repair, only when you say so."

## 4:45–5:00 — The hot take

"The best example of why this matters, ReqGuard found in its own build: our CLI
was reporting success on commands that had actually failed — a green that lied.
Caught by checking the real exit code, not by trusting the output. That's the
exact failure mode this tool exists to catch, and the discipline has to apply
to the tool itself first."

---

## Facts you can cite safely (all verified this session)
- PyJWT: commit `7144e4534c34810f4525dc4578a32addd8212cff`, v2.13.0. Baseline
  365 passed / 4 skipped; mutated (`exp <=`→`exp <`) 365 passed / 4 skipped —
  mutation survives. Source reverted, `git diff` empty. Live agent run earlier:
  15 API calls, ~2 min.
- itsdangerous: commit `672971d…`, baseline 297 passed. Live run found
  `verify_signature`/`unsign` + correct boolean-inversion mutation; rejected at
  the `anchor_unique` gate (over-indented snippet) → INCONCLUSIVE, no false
  pass, repo untouched. Anchor bug since fixed (`reanchor`, 3 tests).
- Benchmark: recall 1.00 vs baseline 0.933, precision 1.00 both, clean-FP 0.0
  both, repair 15/15 — from `reqguard evaluate`.
- `apply --prove` on case13 (offline): repo suite 2→3 tests, green, gap proven
  closed. Full test suite: 237 passed / 4 skipped / 0 failed.

## Do NOT claim on camera
- Don't say PyJWT has a "vulnerability" or "bug" — say "unprotected boundary /
  executable coverage gap."
- Don't show a live itsdangerous "gap found" verdict — that run was
  INCONCLUSIVE; the fix is verified by tests, not yet by a fresh live run.
- Don't quote token cost for the live runs — only API-call counts are reliable.
