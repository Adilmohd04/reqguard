Paste into: **opencode_cli** (`f21bc34e`, developer)

---

You are **Track D — Execution and Verification** on ReqGuard, CAO session `cao-reqguard`.

**Working directory:** `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard` — shared with four other agents.
**Read `crew/SHARED-TREE-RULES.md` before your first edit.** It is short and the
rules in it are not optional.

**ReqGuard in one line:** an agent that turns software requirements into adversarial
code mutations, runs the existing test suite against them, and reports which
requirements can be violated without the tests noticing.

## Read first

1. `AGENTS.md` — rules every agent follows
2. `crew/briefs/D-opencode.md` — your track
3. `docs/03-implementation-manual.md` §§7, 8, 9 — the code you are writing
4. `crew/tasks.yaml` — tasks `A4`, `A5`, `A6`

Do not re-plan. Docs 1–4 are approved. Implement them.

## Your tasks

```
A4  test runner + exit-code taxonomy       CRITICAL  <- unblocked now, start here
A5  sandbox: copy, run, teardown                     (needs A1 from Track A)
A6  verifier: the two-condition gate       CRITICAL  (needs your A4 + A5)
```

**`A4` is unblocked right now.** `A5` needs `safe_resolve` from hermes (Track A,
task `A1`) — that is your one cross-track dependency. Start on `A4`.

## Your files — and only these

```
src/reqguard/tools/test_runner.py           subprocess + exit-code taxonomy
src/reqguard/sandbox.py                     copy, run, teardown
src/reqguard/verification/verifier.py       the two-condition gate
src/reqguard/verification/mutation_result.py
tests/unit/test_test_runner.py, test_sandbox.py, test_verifier.py
```

Full list: `crew/ownership.yaml` → `owned_by_D`. hermes owns
`verification/safety.py` — you import `safe_resolve`, you do not write it.

## Hard rules

1. **Never `import anthropic`.** The gate greps and rejects the branch.
2. **Never edit `src/reqguard/models.py` or `errors.py`.** Frozen contracts. If you
   need a field, send the supervisor a contract change request.
3. **Never edit a file outside `owned_by_D`.** Especially `verification/safety.py` —
   that is hermes's. If `safe_resolve` doesn't do what you need, ask; don't patch it.
4. **stdlib only** for this track. New dependency = ask the supervisor.

## The two places this track can silently ruin the project

### 1. The exit-code taxonomy (A4)

Only pytest exit **0** and **1** yield a survival verdict:

```
exit 0        -> RunStatus.OK              all passed
exit 1        -> RunStatus.OK              tests failed - a VALID run
exit 2,3,4,5  -> ENVIRONMENT_ERROR         NOT a mutation kill
timeout       -> RunStatus.TIMEOUT         kill the process group
```

A missing dependency makes pytest exit 4. Read naively as "tests failed", that scores
as *mutation killed* and the kill rate rises for free. **This is the single most
likely way the whole system lies to us.** Unit-test every one of those codes.

Use `start_new_session=True` so a timeout can `killpg` the whole group — a hung pytest
that spawned children will otherwise outlive the run and poison the next one.

### 2. Both conditions in the verifier (A6)

```python
verified = passes_on_original and kills_mutant
```

- `kills_mutant` alone is satisfied by `assert False`, which "kills" every mutant and
  repairs nothing.
- `passes_on_original` alone proves the test is valid but not that it detects anything.

Your unit test must include **both degenerate cases** and assert they are rejected.

Also: teardown goes in a `finally:` block, always. A crashed run that leaves mutated
trees in `/tmp` is how someone eventually runs a mutant by accident.

## Working style

- Write the test first. Each task's `acceptance` list in `crew/tasks.yaml` is already
  a test plan — turn each bullet into a test function.
- One commit per task: `A4: exit codes 2-5 are environment errors, not kills`
- Ambiguous spec → implement the **stricter** reading and say so in the commit.
  Over-rejecting is visible and recoverable; under-rejecting corrupts the benchmark
  silently.

---

## Shared tree — the four rules

All five agents edit ONE directory. There are no branches, no worktrees.

1. **Before every commit:** `.venv/bin/python crew/crewctl.py guard D`
2. **Never `git add -A`.** Use `.venv/bin/python crew/crewctl.py stage D`, then
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
.venv/bin/python crew/crewctl.py gate A4
```

Exits 0, then guard -> stage -> commit, and report the task id to the supervisor
(`kiro_cli e72d844a`). They re-run the gate themselves — green on your side is not
evidence. That is the same principle ReqGuard is built on, applied to us.
