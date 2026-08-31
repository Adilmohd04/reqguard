# ReqGuard — Claude Code

You are **Track B: Agent Stages** in a three-agent build crew.

Start here, in order:

1. `AGENTS.md` — rules that apply to every agent on this repo
2. `crew/briefs/B-claude-code.md` — your track, your files, your tasks
3. `crew/tasks.yaml` — task ids `B1`–`B6`, each with a gate and acceptance list

## Before writing any Anthropic SDK code

Invoke the `claude-api` skill. Do not write model calls from memory — several
API shapes changed in 2025–2026, and a stale prior costs a 400 at runtime rather
than at review. `docs/03-implementation-manual.md` §1 has the verified shapes.

Four traps, all silent until they aren't:

- `budget_tokens` is a **400** on `claude-opus-5`. Removed. Use `output_config.effort`.
- Assistant prefill is a **400** on `claude-opus-5`. Use structured output.
- Anything interpolated into the cached `system` block kills the cache (5× cost).
- The Python Tool Runner does **not** auto-resume `pause_turn` — it exits with a
  truncated answer and no error. We declare no server tools; assert loudly instead.

## Boundaries

- You own every `import anthropic` in the repo. Tracks A and C may not have one.
- You may **not** edit `src/reqguard/models.py` or `errors.py` — frozen contracts.
- You may **not** edit files outside `owned_by_B` in `crew/ownership.yaml`.
- You **bind** Track A's tools in `tools/registry.py`. Never reimplement them —
  a second copy forks the behaviour and only one of them gets tested.

## Done means the gate passed

```bash
python crew/crewctl.py gate B4
```

The Supervisor re-runs it. Green in your worktree is not evidence.
