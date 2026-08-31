# Supervisor tooling hotfix — expand untracked files in crewctl

## Problem

`crew/crewctl.py::working_tree_files()` runs `git status --porcelain`. Git collapses an entirely untracked directory to a single `dir/` entry, so exact ownership patterns cannot recognize owned files inside it. `guard` incorrectly reports `owned by ?`, and `stage` omits valid owned files. This affected Tracks B and C and can affect every first commit.

## Required change

1. Change the status invocation to request all untracked files (`git status --porcelain --untracked-files=all`, or equivalent arguments).
2. Preserve existing parsing and handling of modified/staged paths.
3. Add a focused regression test under `crew/tests/` that initializes a temporary git repository with a wholly untracked nested directory and proves `working_tree_files()` returns individual file paths, not the collapsed directory. Cover filenames with spaces/quoting if the current parser claims to support them.
4. Run the regression test plus a smoke check of `crewctl guard C` in the current shared tree. Do not modify feature code, ownership rules, contracts, or task statuses.

## Shared-tree constraints

Edit only `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/crew/crewctl.py` and the new focused test path under `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/crew/tests/`. Do not stage or commit: role S also owns dirty supervisor bookkeeping files, so automated S staging would capture unrelated work. Return changed absolute paths and validation output to the supervisor for review.
