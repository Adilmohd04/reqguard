# C5 Review Round 1 — NEEDS_CHANGES

Commit reviewed: `fe1212259aa89a1e0b25e776e9dad003bd6ae6bf`. Supervisor gate passed 28 tests, but the benchmark freeze hash is scientifically unstable.

## Blocking fix

`benchmarks_tree_sha256()` hashes volatile artifacts created by normal validation/test runs, including `__pycache__/`, `*.pyc`, and `.pytest_cache/`. The same committed corpus therefore receives different hashes before and after pytest or across Python versions, defeating Doc 3 §15.6.

Exclude volatile/generated paths deterministically. At minimum mirror relevant A5 exclusions: `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.tox`, `.venv`, `venv`, `node_modules`, compiled Python files, and existing `results/`. Do not exclude benchmark source/ground-truth files. Add tests proving cache creation does not change the hash while source/content changes do.

## Non-blocking observations

Duplicate/conflicting verdict IDs currently use last-wins and unmatched verdicts are ignored; document or harden later. Diff mode-only parsing is also non-blocking.

Run the C5 gate, create a new `C5:` commit (do not amend), and report the hash, changed absolute paths, and gate output. Do not start C6 until C5 is accepted.
