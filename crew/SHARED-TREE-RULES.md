# Shared-Tree Rules

**Every agent reads this. It is short because it has to be followed exactly.**

All five agents run in **one working directory**:

```
/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard
```

There are no per-agent branches and no worktrees. That is not a shortcut — with a
single checkout, `git checkout` by one agent **rewrites files under every other agent
mid-edit**. One branch (`main`) is the safe configuration.

So the collision risk here is not a merge conflict. It is quieter and worse:

> **staging another agent's half-finished work, or overwriting a file you don't own.**

Four rules prevent it.

---

## 1. Before every commit, run the guard

```bash
.venv/bin/python crew/crewctl.py guard <YOUR-ROLE>
```

It lists every dirty path in the tree and splits them into **yours** and **not yours**.
If anything of yours is missing, or anything not yours appears in your intended
commit, stop and fix it before staging.

## 2. Never `git add -A` or `git add .`

In a shared tree those stage every other agent's work in progress. Use:

```bash
.venv/bin/python crew/crewctl.py stage <YOUR-ROLE>
git commit -m "<TASK-ID>: <what changed>"
```

`stage` runs `git add` on **only** the dirty files your role owns. It is the only
staging command you should ever run.

## 3. Never run these — they mutate the tree under everyone else

```
git checkout        git switch         git stash
git reset --hard    git clean          git rebase        git merge
```

Only the supervisor touches branch state. If you think you need one of these, you
have a problem the supervisor should hear about instead.

`git pull` is also the supervisor's. Your files are yours; you don't need to sync.

## 4. Write only files your role owns

`crew/ownership.yaml` is authoritative. Every path has exactly one owner and there is
**zero overlap** — verified. If the file you want isn't in your list, do not edit it:

- **need a new field on a shared contract** (`models.py`, `errors.py`) → contract
  change request to the supervisor, one sentence
- **need a function another track owns** → ask the supervisor to relay. Writing your
  own copy forks the behaviour and only one version ends up tested.

---

## Commit discipline

Commit **early and often** — one task per commit, prefixed with the task id:

```
A3: reject mutations targeting test files
```

The window in which another agent can see your partial state is exactly the time
between your edits and your commit. Keep it short.

If two agents edit at once and one saves over the other, git will not warn you —
there is no branch boundary to conflict on. Small commits are the recovery mechanism.

---

## Python on this box

The bare `python` on PATH is a Windows Store stub. Use the venv:

```bash
.venv/bin/python          # or .venv/Scripts/python.exe from a Windows shell
```

`crewctl` puts its own interpreter at the front of PATH when it runs a gate, so gate
commands written as plain `python ...` / `pytest ...` resolve correctly.

---

## If per-agent working directories become available

Shared-tree mode exists because CAO pins all five agents to one `cwd`. If you can set
a different `cwd` per agent, worktrees are strictly safer — the supervisor runs
`crew/bootstrap.ps1` (worktree mode) instead of `crew/bootstrap.sh`, and rules 2 and 3
relax. Ask the supervisor before switching; do not switch unilaterally.
