---
name: reqguard
description: Check whether a Python project's tests actually protect its written requirements. Use when the user asks if tests cover a requirement or spec, wants to find test gaps, reviews AI-generated code or tests, or asks "would my tests catch this bug". Needs a requirements file and a pytest suite. Not for writing general tests or for measuring coverage.
---

# ReqGuard: requirement integrity checks

ReqGuard answers one question per requirement: if someone broke this requirement
in the code, would the test suite notice?

For each requirement it finds the code that enforces it, applies a small
deliberate violation (a mutation) inside a sandbox copy, and runs the real
pytest suite:

- Tests fail → **PROTECTED**. The suite caught the violation.
- Tests still pass → a gap. ReqGuard then writes a regression test and checks it
  two ways: it must pass on the original code and fail on the mutant.
  - Both checks pass → **REPAIRED** (a verified test is available)
  - The test did not verify → **INSUFFICIENTLY_PROTECTED**
- Anything it could not judge reliably (suite not green, no enforcement site,
  environment error) → **INCONCLUSIVE**, with a reason.

The model proposes. pytest decides. Never report a verdict ReqGuard did not return.

## Tools (MCP server `reqguard`)

| Tool | Model calls | Writes to repo |
|---|---|---|
| `reqguard_check_setup` | no | no |
| `reqguard_init` | no | only new files, never overwrites |
| `reqguard_analyze` | yes | only `reqguard-report/` |
| `reqguard_status` | no | no |
| `reqguard_findings` | no | no |
| `reqguard_apply_repair` | no | yes: one new test file, after user approval |

## Workflow

1. **Check setup first.** Call `reqguard_check_setup` with the repo path.
   Fix what it reports before analyzing. The usual problems:
   - No requirements file: offer `reqguard_init`, then help the user write real
     requirements. One testable rule per bullet, e.g. "A cancelled order cannot
     be shipped." Don't invent requirements the user hasn't confirmed.
   - Suite not green: ReqGuard needs a passing baseline. Fix the tests first.
   - No provider key: the user must set `OPENROUTER_API_KEY` (or
     `CONDUIT_API_KEY`) in their shell or the repo's `.env`. Never ask them to
     paste the key into chat, and never write it into a file yourself.
2. **Analyze.** Call `reqguard_analyze`. To keep runs fast and cheap, start with
   one requirement (`requirement: "REQ-01"`), or use `dry_run: true` to preview
   the planned mutations without running them. Full runs can take several
   minutes. If the result says `running`, poll `reqguard_status` with the
   `run_id`. Don't start a second analysis on the same repo.
3. **Report.** Summarize per requirement: id, severity, status, and for gaps the
   `expected_violation` (what could break unnoticed). Lead with high-severity
   gaps. Point the user to `reqguard-report/summary.html`. Use
   `reqguard_findings` with a `requirement` id to show the mutation diff and the
   proposed test.
4. **Apply only with approval.** For a **REPAIRED** requirement, show the user the
   test source and the target path, and ask them. Only after they say yes, call
   `reqguard_apply_repair` with `confirm: true`. Prefer `prove: true`, which runs
   the repo suite and rolls the write back if it's not green. ReqGuard refuses
   to apply unverified repairs. Don't try to work around that by writing the
   file yourself.

## Rules

- Don't edit source or test files to "fix" a finding unless the user asks.
  ReqGuard's output is evidence for them to act on.
- An INCONCLUSIVE result is not a pass. Report it along with its reason.
- Python/pytest projects only (v1).
