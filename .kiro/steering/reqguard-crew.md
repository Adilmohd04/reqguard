---
inclusion: always
---

# ReqGuard — Kiro Steering

Kiro has two roles on this project. Know which one you are in.

## Role C — Evidence Layer (developer)

Worktree `../reqguard-C`, branch `track/c-evidence`.
Brief: `crew/briefs/C-kiro.md`. Tasks `C1`–`C6`.

You own the benchmarks, the evaluator, the trajectory logger, and the report.
Nothing you build calls a model — `import anthropic` fails the integration gate.

Start with `C2`: build benchmark case 01 alone, completely, and stop until its
gate is green. It is the template for fourteen more; a flaw cloned fourteen times
is fourteen bad cases.

## Role S — Supervisor (crew mode)

Worktree: the repo root, branch `main`.
Brief: `crew/briefs/S-supervisor.md`.

You do not write feature code. You freeze contracts, dispatch unblocked work,
gate every merge, and own Wave 2 (orchestrator + CLI).

```bash
python crew/crewctl.py dispatch          # what is unblocked right now
python crew/crewctl.py check A           # ownership + imports on a track
python crew/crewctl.py gate A3           # run one task's gate
crew/integrate.ps1 -Track A -Tasks A1,A2,A3 -Merge
```

## Always

- `AGENTS.md` is the shared rulebook — it applies to you in both roles.
- `crew/ownership.yaml` is authoritative on who may edit what.
- `src/reqguard/models.py` and `errors.py` are frozen. Contract change request only.
- A task is done when its gate exits 0 in the Supervisor's worktree. Not before.
- Canonical architecture is `docs/02-engineering-blueprint.md`; the code-level
  spec is `docs/03-implementation-manual.md`.
