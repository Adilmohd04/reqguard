# Solution video script (target: 4:30, cap 5:00)

Everything below is real and already verified in this repo — no staging, no
invented numbers. Run the commands live on screen as you narrate; every
number quoted matches what `reqguard evaluate` actually printed this session.

---

## 0:00–0:35 — The problem

**Say:** "Tests passing doesn't mean a requirement is protected. Here's a
real example. This requirement says: an order that's only partially
fulfilled — say 80 of 100 units shipped — must never be reported as
'completed'. The existing test suite passes. Does it actually protect
this rule?"

**Show:** `benchmarks/case13-partial-fulfillment/requirements.md` — the
requirement, on screen, a few seconds.

## 0:35–1:05 — The simple baseline

**Say:** "The obvious baseline: ask an LLM to just look and tell you.
Same model, same tools, one direct question — 'does the test suite
protect this?'"

**Show:**
```bash
cat benchmarks/replay/case13-partial-fulfillment/REQ-13.baseline.json
```
Point at `"status": "PROTECTED"` — the baseline's actual reasoning ("both
status boundaries are tested... looks protected") sounds plausible and is
wrong.

**Say:** "That's wrong. The baseline reasoned its way to a false sense of
safety — no code was ever actually broken to check."

## 1:05–3:20 — One realistic execution, start to finish

**Say:** "ReqGuard does the opposite: it doesn't ask, it breaks the code on
purpose and watches what happens."

**Run (zero cost, deterministic, offline):**
```bash
reqguard analyze benchmarks/case13-partial-fulfillment --replay benchmarks/replay
```

**Narrate as it runs, pointing at each line:**
1. "It reads the requirement and finds the enforcement point in the code —
   `fulfillment_status`'s fallthrough `'partial'` return, the only line
   that stops a partial shipment from reporting complete."
2. "It deliberately mutates that — redirects the partial fallthrough to
   `'completed'` — a change no fixed mutation catalogue entry names, so it's
   tagged `semantic_change` with an explicit rationale, not forced into the
   wrong bucket."
3. "It reruns the real test suite against the mutated code."
4. "The tests still pass. The mutation **survived**. That's the gap — the
   exact thing the baseline missed."
5. "ReqGuard writes a new test — `fulfillment_status(100, 80) == 'partial'`
   — and verifies it two ways: passes on the original code, fails on the
   mutated code. Only then does it count as repaired."

**Show:** `reqguard trajectory benchmarks/case13-partial-fulfillment/reqguard-report/trajectory.jsonl --format md`
— point at the `investigate` → `plan` → `survived` → `repair` → `verified`
arc, all for the one requirement, all closed.

*(Optional, and now genuinely strong: `trajectories/live-case13-gpt5-conduit.md`
is a real, live, paid `gpt-5` run on this exact case — investigate → a real
`boundary_shift` mutation → `survived=True` → repair verified both ways, 8
API calls, no replay. If you want to show a real model doing this instead of
the offline replay, this is a clean, complete, ready-to-narrate success —
not just an honest partial attempt. Either artifact supports the same claim;
replay is zero-risk to narrate live, the real trajectory is more impressive
if you'd rather read from a prepared file than run a live call during
recording.)*

## 3:20–3:40 — Close the gap on the real repo (not just report it)

**Say:** "Finding the gap is half of it. Watch it actually close the gap —
on the real repository, and prove it."

**Run (still offline, zero cost):**
```bash
reqguard apply benchmarks/case13-partial-fulfillment --requirement REQ-13 \
  --report-dir benchmarks/case13-partial-fulfillment/reqguard-report --yes --prove
```

**Narrate, pointing at the output:**
- "It writes the verified repair test into the repo — the only command that
  ever writes to your code."
- "Then it runs the repo's **own** suite: 3 passed, exit 0. The suite went
  from silently green over the gap to genuinely protecting the requirement."
- "And if that suite hadn't been green, it rolls the write back and leaves
  your repo untouched — it never leaves you in a broken state."

**Say:** "That's the whole point in one command: not a report that says
'you have a gap,' but a repository where the gap is closed and proven."

## 3:40–4:05 — The final comparison

**Show the table** (from `reqguard evaluate`, full 18-case corpus):

| Metric | ReqGuard | Baseline |
|---|---|---|
| Gap-detection recall | **1.00** | 0.933 |
| Precision | 1.00 | 1.00 |
| Clean-case false-positive rate | 0.0 | 0.0 |
| Repair success rate | 15/15 | n/a |

**Say:** "Across all 18 cases — 15 with a real defect, 3 clean — ReqGuard
catches every defect. The baseline misses exactly one: this one. Neither
arm ever falsely flags a clean case. And when ReqGuard finds a gap, it also
closes it — 15 out of 15 repairs verified, not just detected."

## 4:05–4:35 — The changelog: what actually helped, and what didn't

**Say:** "Two things mattered most. First, giving the mutation planner an
escape hatch — a `semantic_change` mutation type for gaps no fixed catalogue
entry expresses, like this one. Without it, case13 doesn't go to a wrong
answer, it goes **inconclusive** — which is itself an honest finding, not a
silent failure.

Second — an experiment we tried and dropped: free-tier models for the live
run. They either weren't allowed to reason well enough to find the real
enforcement point, or the provider didn't have billing enabled at all. That
taught us something real: this kind of agentic, tool-driven mutation
testing needs a genuinely tool-competent model — not just any LLM that can
chat."

## 4:35–5:00 — The hot take

**Say:** "The best example of the whole idea, ReqGuard found in its own
build: our CLI's exit-code handling had a real bug — every failing command,
including our own go/no-go gate, was silently reporting success to the
shell. Caught by a human actually checking the real exit code — not by the
tool. That's the exact failure mode ReqGuard exists to catch, and it's a
good reminder that the discipline has to apply to the tool itself, not just
the code it's checking."

**Close:** point at the repo, `COLLAB.md`, and `CHANGELOG.md` as where the
full story — including every provider swap and every real bug — is written
down as it happened, not reconstructed after the fact.

---

## Recording checklist

- [ ] Terminal font large enough to read on a recording
- [ ] Run `reqguard evaluate --results ...` once beforehand to know the exact
      dir name (avoids dead air finding it live)
- [ ] Confirm `.env` doesn't leak on screen if any terminal shows env vars
- [ ] Practice once end-to-end before the real take — this script runs long
      if read verbatim; trim narration, not commands
