# Track B recovery assignment — adopt existing B3–B6 WIP

Substantial uncommitted B-owned implementations already exist in the shared tree. Adopt them rather than rewriting blindly.

## Scope

Work through tasks in dependency order: **B3 → B4 → B5 → B6**. Read each acceptance checklist in `crew/tasks.yaml`, `crew/briefs/B-claude-code.md`, and the referenced Doc 3 sections. The current A2 files are present but under review; do not edit Track A files.

## Required work now

1. Audit authorship-independent correctness of every dirty B-owned file and the combined `tests/integration/test_stages.py` changes.
2. Run gates in dependency order. Fix only B-owned files until all four gates are green under CI-safe fakes; live tests must skip without credentials.
3. Preserve the fairness rule: B6 uses the same model, xhigh effort, and the same sorted read-only tool surface.
4. Do not stage or commit yet. The shared test file contains cross-task changes and A2 must be accepted first. Return a per-task report: files attributable to each task, gate output, remaining dependency issues, and a proposed dependency-safe commit split. The supervisor will authorize integration after A2 acceptance and review.

Never run checkout/switch/stash/reset/clean/rebase/merge/pull, and never edit contracts or non-B files.
