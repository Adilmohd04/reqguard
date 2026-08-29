# Brief A — Deterministic Core

**Tool:** hermes (CAO agent `c8b12401`, role: developer)
**Branch:** `track/a-core`
**Worktree:** `../reqguard-A`
**Tasks:** `A1 → A2, A3, A4, A5 → A6` (see `crew/tasks.yaml`)

---

## Why you have this track

Everything you build runs **with no API key and no model call**. That is the point.
Your track is the half of ReqGuard that makes the other half trustworthy: the agent
proposes, your tools execute, your verifier decides. If your code is wrong, every
number in the submission is wrong and nothing downstream will notice.

It is also fully specified and fully testable, which means you can self-verify with
`pytest` rather than waiting on anyone.

---

## Your files — and only these

```
src/reqguard/tools/repository.py      the 7 read tools
src/reqguard/tools/mutation.py        apply + the 6 gates
src/reqguard/tools/test_runner.py     subprocess + exit-code taxonomy
src/reqguard/tools/diff.py
src/reqguard/verification/safety.py   safe_resolve
src/reqguard/verification/verifier.py the two-condition gate
src/reqguard/sandbox.py
tests/unit/test_*.py                  for each of the above
```

Full list: `crew/ownership.yaml` → `owned_by_A`.

## Hard rules

1. **Never `import anthropic`.** Not once. Your track must run in CI with no key.
   The integration gate greps for it and rejects the branch.
2. **Never edit `src/reqguard/models.py` or `errors.py`.** They are frozen. Import
   from them. If you need a field that isn't there, file a contract change request
   (`docs/04-build-crew-protocol.md` §6) — do not add it yourself.
3. **Never edit a file outside your ownership list.** The gate checks.
4. Read from `docs/03-implementation-manual.md`. Sections 4, 6, 7, 8, 9 are yours.

---

## The three places this track can silently ruin the project

### 1. `not_a_test_file` — task A3

> A mutation may never modify a test file.

If the mutation engine can touch tests, "the tests didn't catch it" becomes trivially
achievable and the primary metric means nothing.

```python
def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (p.name.startswith("test_") or p.name.endswith("_test.py")
            or p.name == "conftest.py" or "tests" in p.parts)
```

Write `tests/unit/test_mutation.py::test_rejects_test_files` **first**, before the
implementation. It is the most important test in the repository.

### 2. The exit-code taxonomy — task A4

Only pytest exit **0** and **1** yield a survival verdict. Everything else is
`ENVIRONMENT_ERROR`.

A missing dependency makes pytest exit 4. Read naively as "tests failed", that scores
as *mutation killed* and the kill rate rises for free. This is the single most likely
way the system lies to us.

```python
case 0: RunStatus.OK                              # all passed
case 1: RunStatus.OK                              # tests failed — a VALID run
case 2 | 3 | 4 | 5: RunStatus.ENVIRONMENT_ERROR   # NOT a kill
```

Cover every one of these in a unit test, including timeout.

### 3. Both conditions in the verifier — task A6

```python
verified = passes_on_original and kills_mutant
```

`kills_mutant` alone is satisfied by `assert False`, which "kills" every mutant and
repairs nothing. `passes_on_original` alone proves the test is valid but not that it
detects anything. Your unit test must include both degenerate cases and assert they
are **rejected**.

---

## Definition of done, per task

The `gate` command in `crew/tasks.yaml` exits 0 — run from a clean worktree.
Then push and tell the Supervisor the task id. The Supervisor re-runs the gate
in their worktree; green in yours is not evidence.

---

## Working style that fits this track

- Write the test first. Every task's `acceptance` list in `tasks.yaml` is already a
  test plan — turn each bullet into a test function.
- stdlib + pydantic only. No new dependencies without asking the Supervisor.
  `ast.parse` is sufficient for the mutant-parses gate; you do not need `libcst`.
- Small commits, one per task id, message prefixed `A3:` etc.
- If a spec section is ambiguous, implement the **stricter** reading and say so in
  the commit. In this track, over-rejecting is recoverable; under-rejecting corrupts
  the benchmark silently.
