# ReqGuard — Build Crew Protocol

**Document 4: WHO BUILDS WHAT**
**Version:** 1.0
**Purpose:** Let three coding agents build ReqGuard in parallel without corrupting each other's work.

---

## 0. Scope — read this first

This document is about **building** ReqGuard. It adds nothing to ReqGuard's runtime.

Doc 2 §6 is explicit:

> The main agent should not be a swarm. [...] We don't want Agent 1 talks to Agent 2, Agent 2 talks to Agent 3 [...] just for presentation.

That stands. ReqGuard ships as **one orchestrating agent with four reasoning stages**
(Requirement Analyzer, Repository Investigator, Mutation Planner, Test Repairer) plus a
deterministic core. Nothing here changes that.

The crew is a **development-time** arrangement — three coding tools, three disjoint
tracks, one supervisor. It disappears the moment the code is written.

| | ReqGuard's four stages | The build crew |
|---|---|---|
| Exists at | runtime | development time only |
| Defined in | Docs 1–3 | this document |
| Ships to users | yes | no |
| Count | 4 stages, 1 agent | 3 developers + 1 supervisor |

---

## 1. The actual problem

Three coding agents on one repository collide in three ways, and only one of them
is loud:

| Collision | How it shows up |
|---|---|
| **Two agents edit one file** | merge conflict — loud, annoying, survivable |
| **One agent changes a shared contract** | the other two build against a type that no longer exists — quiet, discovered hours later |
| **Two agents implement the same thing** | two versions of `read_file`, one tested — silent, and it survives to the demo |

The second and third are the dangerous ones. Everything below exists to make them
impossible rather than merely unlikely.

Three mechanisms, in order of importance:

1. **Freeze the shared contract before anyone forks.** One writer, once, in Wave 0.
2. **Disjoint file ownership.** Every file has exactly one owner; the gate enforces it.
3. **Separate worktrees.** Agents cannot even see each other's working tree.

---

## 2. The crew

| Role | Tool | Track | Owns | Needs API key |
|---|---|---|---|---|
| **S** Supervisor | Kiro (crew mode) or human | `main` | contracts, integration, Wave 2 | no |
| **A** Deterministic Core | OpenCode | `track/a-core` | sandbox, patcher, runner, verifier | **no** |
| **B** Agent Stages | Claude Code | `track/b-agent` | all four LLM stages + prompts | yes |
| **C** Evidence Layer | Kiro | `track/c-evidence` | benchmarks, evaluator, reporting | **no** |

### 2.1 Why each tool got its track

Not arbitrary. Each assignment is a fit between the track's dominant risk and the
tool's strength.

**OpenCode → deterministic core.** This track is fully specified in Doc 3 §§4, 6–9,
uses stdlib plus pydantic, and is verifiable entirely with `pytest`. It needs no
external API knowledge and no secrets, so the agent can self-verify every change
without a round trip. Precise spec in, testable code out.

**Claude Code → agent stages.** This is the one track where being wrong about an API
costs the most: `budget_tokens` is a 400 on Opus 5, prefill is a 400, the Tool Runner
silently truncates on `pause_turn`. Claude Code carries the `claude-api` skill and can
check current SDK shapes rather than recalling them. Concentrating every model call in
one track also means an SDK change touches exactly one worktree.

**Kiro → evidence layer.** Fifteen benchmark cases are near-identical structures
generated from one template against a fixed schema — spec → tasks → implementation,
which is what Kiro's workflow is built around. Kiro also supervises, because the
supervisor's job is dispatch and gating rather than authorship.

### 2.2 Why the tracks are independent

The dependency graph was cut so that **two of the three tracks need no API key** and
**Track C depends on nothing from A or B**.

That has a practical consequence worth stating: if the model budget runs out, or an
API key is missing, or Track B stalls entirely — the deterministic core and the whole
evidence layer keep building. Two thirds of the project is immune to the failure mode
most likely to hit a hackathon.

---

## 3. Waves

```
WAVE 0   Supervisor alone
         S0.1  freeze models.py + errors.py      ← done
         S0.2  bootstrap: repo, branches, worktrees
         S0.3  package skeleton + pyproject
                             │
                             ▼
WAVE 1   All three in parallel — no shared files
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   A  OpenCode          B  Claude Code      C  Kiro
   A1 safe_resolve      B1 client+policy    C1 trajectory
   A2 7 read tools      B2 analyzer         C2 case01  ←── template
   A3 mutation gates    B3 investigator*    C3 cases 02-15 + 3 clean
   A4 test runner       B4 planner*         C4 validate.py
   A5 sandbox           B5 repairer         C5 evaluator
   A6 verifier          B6 baseline         C6 reporting
         └───────────────────┼───────────────────┘
                             ▼
WAVE 2   Supervisor alone — the only code importing all three tracks
         S2.1 orchestrator → S2.2 CLI → S2.3 doctor → S2.4 docker → S2.5 benchmark
```

`*` B3 and B4 need A2 and A3 merged. Track B starts on B1, B2, and all five prompt
files — none of which need anything from A. **Being blocked is the dependency graph
being honest, not a stall.**

Wave 2 belongs to the Supervisor because `orchestrator.py` and `cli/main.py` are the
only files that import from all three tracks. They are written last, when the
interfaces are real rather than predicted.

---

## 4. Dispatch protocol

The Supervisor computes readiness; nobody guesses it.

```bash
python crew/crewctl.py dispatch
```

A task is dispatchable when every id in its `needs` list has `status: done`.

To dispatch, hand the agent **two things**: its brief, and the task id. Nothing else.
Do not paste the spec — each brief already names the sections it needs, and pasting
more invites the agent to wander outside its ownership.

```
Track A, task A3.  Brief: crew/briefs/A-opencode.md.
```

That is the whole message.

---

## 5. Integration gate

No task becomes `done` on an agent's report. It becomes done when its gate exits 0
**in the Supervisor's worktree**.

```powershell
crew\integrate.ps1 -Track A -Tasks A1,A2,A3          # dry run
crew\integrate.ps1 -Track A -Tasks A1,A2,A3 -Merge   # gate, then merge
```

Four checks, in order, stopping at the first failure:

| # | Check | Rejects |
|---|---|---|
| 1 | Ownership | a branch that touched a path it doesn't own |
| 2 | Imports | `anthropic` in A or C; `reqguard.agent` in A or C |
| 3 | Gates | any task whose gate command doesn't exit 0 |
| 4 | Merge | only reached when 1–3 are clean |

**Reject with the specific violation.** "Please fix" teaches nothing. "You modified
`src/reqguard/models.py`, which is owned by S — revert it and open a contract change
request" is actionable and takes the same ten seconds.

> A merge conflict under this scheme means two tracks wrote one file. Fix
> `ownership.yaml`; do not just resolve the conflict and move on. The conflict is the
> symptom, the ownership map is the bug.

Green in the agent's own worktree is not evidence. This is the same principle
ReqGuard itself is built on — the agent proposes, the verifier decides — applied to
the crew that builds it.

---

## 6. Contract change requests

`src/reqguard/models.py` and `src/reqguard/errors.py` are frozen. A track that needs a
change sends the Supervisor one message:

```
CONTRACT CHANGE — Track A, task A3
Need:   MutationSpec.applied_line_count: int
Why:    the small_diff gate reports the count in its rejection; recomputing it
        in the evaluator would duplicate the diff logic in two tracks
Breaks: nothing — new optional field with a default
```

The Supervisor then, in order:

1. edits the contract on `main`
2. runs every track's gates against the change
3. commits, and **broadcasts to all three tracks** — including the two that didn't ask

Step 3 is the one people skip. A contract that changes without a broadcast is worse
than one that never changed, because two tracks are now building against a model they
believe they know.

**Never edit a contract inside a track worktree.** It will merge cleanly and break the
other two silently.

---

## 7. Failure modes and responses

| Symptom | Likely cause | Response |
|---|---|---|
| Merge conflict | two tracks own one file | fix `ownership.yaml`, re-cut the task |
| Track B blocked on A | correct — B3/B4 need A2/A3 | B writes prompts; those need nothing from A |
| Gate passes for the agent, fails for S | uncommitted file, or environment drift | gate always runs in S's worktree — that's why |
| `import anthropic` in A or C | agent reached for the SDK out of habit | gate rejects; restate rule 3 in `AGENTS.md` |
| Two implementations of one function | ownership map was ambiguous | delete the copy, tighten the map, add the path explicitly |
| An agent edits a frozen contract | it needed a field and didn't ask | revert, run the §6 process, broadcast |
| Everything blocked | Wave 0 not finished | that's the design — finish S0.2 and S0.3 |

---

## 8. Mapping to the three-day schedule

Doc 2 §36's schedule, with the crew overlaid:

| | Supervisor | OpenCode (A) | Claude Code (B) | Kiro (C) |
|---|---|---|---|---|
| **Day 1** | Wave 0, then gate A | A1–A5 — **no key needed** | B1, B2, all prompts | C1, C2 — **no key needed** |
| **Day 2** | gate + merge all tracks | A6 | B3–B6 | C3–C6 |
| **Day 3** | Wave 2: S2.1–S2.5 | support / fixes | prompt tuning against real failures | benchmark runs, report |

Doc 2 §36's Day 1 rule — *do not move forward until the end-to-end case works* —
belongs to the Supervisor, and it is checked by `S2.3`:

```bash
reqguard doctor --case benchmarks/case01-cancelled-order-shipping
```

Six kill criteria, printed pass/fail. If it is not green at the end of Day 2, take the
documented response in Doc 3 §20 — narrow the scope. The last row of that table says
*reconsider the project*. It means it.

---

## 9. What the crew must not become

Three failure patterns, worth naming so they can be spotted early:

**Agents negotiating with each other.** They don't talk. Every cross-track question
goes through the Supervisor. Two agents agreeing on an interface in a side channel is
exactly how you get two implementations of `read_file`.

**A supervisor that writes code.** Then it isn't gating, it's just a fourth developer
with extra permissions, and nobody is checking the merges.

**Parallelism as theatre.** If a track's work is genuinely sequential, run it
sequentially. Three agents is a means of finishing a three-day build, not a thing to
demonstrate. Doc 2 §6 says this about ReqGuard's runtime; it is equally true of its
construction.

---

## 10. Quick reference

```bash
# Supervisor
powershell -ExecutionPolicy Bypass -File crew\bootstrap.ps1   # once
python crew/crewctl.py board                                  # full board
python crew/crewctl.py dispatch                               # what's unblocked
python crew/crewctl.py check A                                # ownership + imports
python crew/crewctl.py gate A3                                # one gate
crew\integrate.ps1 -Track A -Tasks A1,A2,A3 -Merge            # gate + merge

# Track A            cd ../reqguard-A  &&  opencode
# Track B            cd ../reqguard-B  &&  claude
# Track C            cd ../reqguard-C  &&  kiro .
```

| File | What it is |
|---|---|
| `crew/ownership.yaml` | who may edit what — authoritative |
| `crew/tasks.yaml` | the board: tasks, deps, gates, acceptance criteria |
| `crew/briefs/*.md` | what each agent is actually told |
| `crew/crewctl.py` | dispatch, ownership check, gate runner |
| `crew/bootstrap.ps1` | repo + branches + worktrees, once |
| `crew/integrate.ps1` | the merge gate |
| `AGENTS.md` | shared rules — every tool reads it |
| `CLAUDE.md` | Claude Code entry point |
| `.kiro/steering/` | Kiro entry point, both roles |

---

*End of Document 4. Wave 0 is done; run `crew/bootstrap.ps1` to open Wave 1.*
