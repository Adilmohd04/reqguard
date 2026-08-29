# ReqGuard — Rules for Every Coding Agent

Read this before your first edit. It applies to OpenCode, Claude Code, and Kiro
alike. Your track-specific brief is in `crew/briefs/`.

ReqGuard is being built by three coding agents working in parallel worktrees,
coordinated by a supervisor. These rules are what make that safe.

---

## 1. Find out what you own, before you edit

`crew/ownership.yaml` maps every file to exactly one owner. **Two agents never
edit one file.** If the file you want to change is not in your list, stop and ask
the Supervisor — do not edit it "just this once".

The integration gate rejects a branch that touched a path it does not own. Not a
warning; a rejection, before merge.

```bash
python crew/crewctl.py check A     # your track letter
```

## 2. The contract is frozen

`src/reqguard/models.py` and `src/reqguard/errors.py` are owned by the Supervisor
and were frozen before any track forked. Every track imports from them; nothing
else is shared.

Editing them in your worktree silently invalidates work in the other two. If you
need a field that isn't there, open a **contract change request**
(`docs/04-build-crew-protocol.md` §6) — one sentence to the Supervisor.

## 3. Only Track B may import `anthropic`

One track owns every model call in the repository. Tracks A and C must run in CI
with no API key, so the evaluator can re-score stored artifacts for free and the
deterministic core can be tested without spending anything.

`import anthropic` in Track A or C fails the gate.

## 4. A task is done when its gate passes — not when you say so

Every task in `crew/tasks.yaml` has a `gate` command. It must exit 0. The
Supervisor re-runs it in their worktree; green in yours is not evidence.

```bash
python crew/crewctl.py gate A3
```

Self-certification is precisely the failure mode ReqGuard exists to detect. It
would be embarrassing to build it any other way.

## 5. Read the spec section, not the whole spec

| Doc | Read it for |
|---|---|
| `docs/01-product-spec.md` | what ReqGuard is, and what it deliberately is not |
| `docs/02-engineering-blueprint.md` | architecture and priorities — **canonical** |
| `docs/03-implementation-manual.md` | the code you are writing. Your brief names the sections |
| `docs/04-build-crew-protocol.md` | how the three of you avoid each other |

Your brief in `crew/briefs/` points at the sections that are yours. Start there.

---

## The three rules that protect the result

These are load-bearing. A defect in any one makes every number in the submission
meaningless, and nothing downstream will notice.

### A mutation may never modify a test file

If the mutation engine can touch tests, "the tests didn't catch it" becomes
trivially achievable. Enforced in three places: the gate in `tools/mutation.py`,
a standing unit test, and a re-check from recorded diffs in the evaluator.

### Only pytest exit 0 and 1 yield a verdict

Everything else is `ENVIRONMENT_ERROR`. A missing dependency makes pytest exit 4;
read as "tests failed" it scores as a mutation kill and the kill rate rises for
free.

### The baseline gets the same model at the highest effort

If ReqGuard wins, it must win on workflow. A handicapped control invalidates the
whole experiment.

---

## House style

- Python 3.12+, type hints on public functions, `from __future__ import annotations`
- stdlib + the pinned dependencies. New dependency = ask the Supervisor first
- Tests live beside the code they cover, named for the task id where useful
- Commit messages prefixed with the task id: `A3: reject mutations targeting test files`
- Small commits, one task each. A commit spanning three tasks cannot be reverted cleanly

## When a spec is ambiguous

Implement the **stricter** reading and say so in the commit message.

Across this codebase, over-rejecting is recoverable and visible; under-rejecting
corrupts the benchmark silently. When in doubt, reject and surface it.
