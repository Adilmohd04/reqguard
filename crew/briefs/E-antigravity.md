# Brief E — Benchmark Corpus

**Tool:** antigravity (CAO role: developer)
**Branch:** `track/e-bench`
**Worktree:** `../reqguard-E`
**Tasks:** `C2 -> C3, C4` (see `crew/tasks.yaml`)

You build the ground truth the entire project is measured against: 18 small runnable
Python projects, each with a requirement, an implementation, a test suite, and a known
correct answer about whether that suite protects the requirement.

Your track depends on nothing from any other track.

Full operating prompt: `crew/prompts/05-antigravity-track-e.md`.
Spec sections: `docs/03-implementation-manual.md` 15; `docs/01-product-spec.md` 29.

## Files you own
```
benchmarks/**                              18 cases + validate.py
src/reqguard/evaluation/ground_truth.py
tests/unit/test_ground_truth.py
```

## Non-negotiable
- Build case01 completely, and STOP until its gate is green. It is the template for
  seventeen more; a flaw cloned seventeen times is seventeen bad cases.
- Every case suite must be GREEN on unmutated code. A red suite voids the case.
- The defect in each case is an ABSENCE in the test suite. Do not "helpfully" fill it.
- 3 clean cases, not 2 -- with 2, the false-positive rate can only be 0/50/100%.
- Once merged, a case does not change after results are seen (Doc 2 section 25).
- Never `import anthropic`. Never edit `models.py` / `errors.py`.
