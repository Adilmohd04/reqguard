# Brief D — Execution and Verification

**Tool:** opencode (CAO role: developer)
**Branch:** `track/d-exec`
**Worktree:** `../reqguard-D`
**Tasks:** `A4 -> A5 -> A6` (see `crew/tasks.yaml`)

You are the half of ReqGuard that turns evidence into a verdict. No model touches this
path by design: if an LLM could assert "the mutation survived", the result would be
self-certified and worthless.

Full operating prompt: `crew/prompts/04-opencode-track-d.md`.
Spec sections: `docs/03-implementation-manual.md` 7, 8, 9.

## Files you own
```
src/reqguard/tools/test_runner.py
src/reqguard/sandbox.py
src/reqguard/verification/verifier.py
src/reqguard/verification/mutation_result.py
tests/unit/test_test_runner.py, test_sandbox.py, test_verifier.py
```
`verification/safety.py` belongs to Track A (hermes). You import `safe_resolve`;
you never edit it.

## Non-negotiable
- Only pytest exit 0 and 1 yield a verdict. 2/3/4/5 and timeout are ENVIRONMENT_ERROR.
- `verified = passes_on_original and kills_mutant` -- both, tested with both
  degenerate cases (`assert False`, and a test that passes everywhere).
- Teardown in `finally:`, always.
- Never `import anthropic`. Never edit `models.py` / `errors.py`.
