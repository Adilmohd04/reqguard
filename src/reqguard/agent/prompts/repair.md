A requirement violation was introduced into the code and the existing test suite
did not detect it. Write the missing test.

You are given: the requirement, the mutation diff, the violation scenario, the
test output from the mutated run, and the existing tests.

Your test must satisfy BOTH conditions:

  A. It PASSES against the current, correct implementation.
  B. It FAILS when the mutation is applied.

A test that fails everywhere (`assert False`) satisfies B and is worthless. A
test that passes everywhere satisfies A and detects nothing. Both are rejected
automatically, and you will be re-prompted with the reason.

Produce a structured proposal:

- `requirement_id`, `mutation_id`: copy from the inputs.
- `test_path`: where the test file should live, following the existing suite's
  layout (repo-relative).
- `test_source`: the COMPLETE file contents, ready to write to disk.
- `explanation`: what the test checks and why it detects this violation.
- `rationale`: ONE sentence naming the assertion that does the work.

Requirements for `test_source`:

- Follow the existing suite's conventions exactly: fixtures, imports, naming,
  style, assertion library.
- One focused test. Not a suite, not a parametrized sweep.
- Construct the precondition state the requirement names (e.g. actually build a
  cancelled order) — the gap is usually that no existing test does.
- Assert on the requirement's `expected_result` — the rejection — not on
  incidental implementation details that the mutation happens to change.
- Use only what the project already depends on. Add no new imports beyond the
  project's existing dependencies.
