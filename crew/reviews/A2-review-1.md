# A2 Review Round 1 — NEEDS_CHANGES

The supervisor gate passed 11 tests, but independent review found LLM-facing robustness gaps.

## Blocking fixes

1. `read_file` currently propagates `FileNotFoundError` and `UnicodeDecodeError`, unlike the other defensive repository tools. Define a recoverable, deterministic response for missing/unreadable/binary files (or a documented domain exception that the tool binding can surface without crashing the tool loop), and test both paths.
2. `search_code` propagates raw `re.error` for malformed model-supplied patterns. Catch invalid regular expressions and return/surface a deterministic recoverable error; add a regression test.

## Required strengthening

Add focused tests for actual `limit` truncation in list/search and for undecodable files being skipped where applicable. Keep all path access routed through `safe_resolve`; do not change contracts or dependencies. `git_diff` already meets the explicit non-git requirement; its broader error distinction is non-blocking.

Run the A2 gate, create a new `A2:` commit (do not amend), and report changed absolute paths and gate output. Edit only A2-owned files and do not start A3 until A2 is accepted.
