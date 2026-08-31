# ReqGuard — Engineering Specification

**Document 2: HOW We Are Building It**
**Version:** 1.0
**Purpose:** Translate `01-product-spec.md` into an implementable system
**Scope guard:** Three days. Nothing in this document exists to be impressive.

---

## 0. What This Document Does Not Do

It does not redefine the product. Every requirement here traces to a section of Document 1. Where this document makes a call Document 1 left open, the call is marked **[DECISION]** with its reasoning and its rejected alternatives, so a reviewer can overrule it in one edit rather than reverse-engineering it later.

---

## 1. Decision Summary

| Area | Decision | Section |
|---|---|---|
| Model | `claude-opus-5` for all reasoning stages; same model for the baseline | §2 |
| Agent runtime | Anthropic Python SDK **Tool Runner** (`client.beta.messages.tool_runner`), self-hosted | §3 |
| Managed Agents | **Not in v1.** Tests must run against the user's real local dependencies | §3.3 |
| MCP | **Not in v1.** Tool layer designed as a plain module so an MCP adapter is ~80 lines later | §3.4 |
| Target language | **Python + pytest only** in v1. Config-shaped for more; not implemented | §4 |
| Mutation mechanism | LLM emits a typed `MutationSpec`; a deterministic patcher applies it by exact anchor match | §7 |
| Sandbox | Copy-tree into a temp dir by default; `git worktree` opt-in for speed | §8 |
| Verdict authority | Never the LLM. `pytest` exit codes only | §9 |
| Verification | **Two-condition gate** — the repair test must pass on original AND fail on mutant | §10 |
| Report | Rich-rendered terminal + `reqguard-report/` (JSON + Markdown) | §13 |
| Packaging | `uv` + `pyproject.toml`; Docker for reproducible benchmark runs | §16 |

---

## 2. Model and Inference Policy

### 2.1 Model choice **[DECISION]**

`claude-opus-5` (`$5 / $25` per MTok, 1M context) for every reasoning stage.

Rejected:
- **Sonnet 5 for cost.** The killer case (§29 of Doc 1) is precisely a semantic-reasoning task. Downgrading the model to save money on a 20-case benchmark is optimising the wrong variable — total benchmark spend is small (§2.4).
- **Mixed models per stage.** Prompt caches are model-scoped. A cascade forfeits cache reuse across stages and adds a confound to the experiment. If cost becomes a real constraint, the lever is `effort`, not model — measured, then reported.

### 2.2 Thinking and effort per stage

Adaptive thinking everywhere (`thinking={"type": "adaptive"}`). Thinking is on by default on Opus 5; we set it explicitly so the intent is legible in the code.

`display: "summarized"` is set **on purpose**: the summarized reasoning is what we write into the trajectory log (§12). The default (`omitted`) would leave us with empty thinking blocks and a much weaker submission artifact.

| Stage | Effort | Why |
|---|---|---|
| 1. Requirement Analyzer | `medium` | Structured extraction from prose. Cheap, high-accuracy. |
| 2. Repository Investigator | `high` | Multi-file search and correlation. |
| 3. Mutation Planner | `xhigh` | The hardest reasoning in the system. This is where the product lives. |
| 5. Test Repair | `high` | Constrained by the investigation findings already in context. |
| **Baseline (§15)** | `xhigh` | **Deliberately the highest.** See §2.3. |

### 2.3 Baseline fairness — a stated integrity rule

The baseline gets the **same model** and the **highest effort setting**. If ReqGuard wins, it must win on workflow, not on a handicapped control.

Anything else makes the headline number meaningless, and a judge will find it in thirty seconds.

Concretely, the only differences between baseline and ReqGuard are:

```
SAME:  model, effort, repository, requirements, test suite,
       read-only tool surface, evaluator

DIFFERENT:  the workflow only
            baseline  → one agent, "review whether tests cover this"
            reqguard  → analyze → investigate → mutate → execute → repair → verify
```

### 2.4 Cost envelope

Rough per-case estimate: ~6 API calls, ~40K input / ~8K output tokens → **~$0.40/case**. A full 20-case benchmark across baseline + ReqGuard ≈ **$16 per run**. Budget for ~10 full runs during development: **~$160**. Tracked for real via §12, never estimated in the final report.

### 2.5 Prompt caching

Each stage has a **frozen** system prompt loaded from `prompts/*.md` at import time — no `datetime.now()`, no run IDs, no interpolated paths in the cached prefix. Tool definitions are emitted in a deterministic sorted order.

```python
system=[{"type": "text", "text": STAGE_SYSTEM, "cache_control": {"type": "ephemeral"}}]
```

Volatile content (the specific requirement, the file contents) goes into `messages`, after the breakpoint. CI asserts `usage.cache_read_input_tokens > 0` on the second case of a benchmark run — a silent cache invalidation is a 5× cost regression that otherwise goes unnoticed.

### 2.6 Request shape

`max_tokens=16000` non-streaming for Stages 1 and 3. Stages 2 and 5 run through the Tool Runner. No `budget_tokens` anywhere (removed on Opus 5 — returns 400). No assistant prefill (returns 400 on Opus 5).

---

## 3. Agent Runtime

### 3.1 The choice **[DECISION]**

**Anthropic Python SDK Tool Runner**, self-hosted.

Of the four ways to build an agent on Claude:

| Option | Verdict |
|---|---|
| Manual `while stop_reason == "tool_use"` loop | Rejected — code we'd own for no benefit |
| **Tool Runner** (`client.beta.messages.tool_runner`) | **Chosen** — SDK drives the loop, we define the tools, we host the compute |
| Managed Agents | Rejected for v1 — see §3.3 |
| Claude Agent SDK | Rejected as the product; **considered as a baseline variant**, see §15.2 |

We must host the compute because **the tests run against the user's real repository and its real installed dependencies**. That is not negotiable — a test suite executed against a guessed dependency set proves nothing.

The Tool Runner is beta. That is an accepted risk: the fallback (a manual loop) is ~40 lines and lives behind the same `run_stage()` interface.

Known Tool Runner caveat — the Python runner does not auto-resume `stop_reason: "pause_turn"`. **Not applicable to us**: `pause_turn` arises from server-side tools, and ReqGuard declares none. We assert on it anyway and fail loudly rather than silently truncating:

```python
if last is not None and last.stop_reason == "pause_turn":
    raise ReqGuardInternalError("unexpected pause_turn - no server tools are declared")
```

### 3.2 Not every stage is an agent

This is the most important structural decision in the system, and it follows directly from Doc 1 §14 (*the agent cannot say "I fixed it"*).

```
Stage 1  Requirement Analyzer   →  structured output   (messages.parse, no tools)
Stage 2  Repository Investigator→  AGENT LOOP          (tool runner, read-only tools)
Stage 3  Mutation Planner       →  structured output   (messages.parse, no tools)
Stage 4  Mutation Executor      →  PURE PYTHON         (no LLM at all)
Stage 5  Test Repair            →  AGENT LOOP          (tool runner, read + submit_test)
Stage 6  Verification           →  PURE PYTHON         (no LLM at all)
Stage 7  Report                 →  PURE PYTHON         (no LLM at all)
```

Two agent loops. Two structured extractions. Three deterministic stages.

The LLM is never given a tool that returns a verdict, and never given a tool that can write to the user's repository. It cannot observe "the mutation survived" and then assert it — the pipeline computes that from a process exit code and hands it to the agent as an immutable fact.

### 3.3 Why not Managed Agents **[DECISION]**

Managed Agents is genuinely attractive here — Anthropic hosts the loop *and* a per-session sandbox, which is exactly the isolation model in Doc 1 §19. Rejected for v1 on three grounds:

1. **Dependency fidelity.** ReqGuard's core claim is *your existing tests didn't catch this*. That requires the user's actual environment — their pinned versions, their conftest, their fixtures. A remote container would need the repo uploaded and dependencies resolved from scratch; any resolution difference invalidates the result.
2. **Ships as a CLI.** Doc 1 §16 is explicit: `reqguard analyze .` on a local repo. A local CLI whose test execution round-trips through a remote sandbox is slower and more fragile for zero user-visible gain.
3. **Three-day scope.** It is a larger platform surface (agents, environments, sessions, event streams) than the problem needs.

**Revisit when:** ReqGuard runs in CI against many repos, or scheduled deployments become a feature. The `engine/` layer is deliberately isolated behind an interface so swapping the executor is a contained change.

### 3.4 Why not MCP in v1 **[DECISION]**

Doc 1 §17 puts CLI, MCP, and SDK on equal footing as *interfaces over one core*. That architecture is honored here — but only the CLI is built.

The enabling constraint: **all repository tools are plain Python functions with typed signatures and no Anthropic imports.** `tools/repo.py` knows nothing about Claude. The `@beta_tool` decorators live in `tools/registry.py`, a thin binding layer. An MCP server is then a second thin binding layer over the same functions.

```
tools/repo.py          ← pure functions, no LLM dependency
    ├── tools/registry.py     (@beta_tool bindings)      → v1
    └── mcp/server.py         (MCP tool bindings)        → post-hackathon
```

Building the MCP surface in v1 would spend a half-day proving an integration nobody demos.

---

## 4. Language Scope **[DECISION]**

**v1 supports Python projects with a pytest suite. Only.**

Reasoning: the mutation validator (§7.4) parses the mutated file with `ast.parse` to reject syntactically broken mutants. That's a per-language capability. The test runner parses pytest's exit codes and JSON report. That's per-framework. Supporting a second language means a second validator and a second runner, and buys nothing for the demo.

`reqguard.yaml` carries `language:` and `test_command:` from day one so the extension point is visible, and the CLI fails with a clear message rather than misbehaving:

```
$ reqguard analyze ./my-go-project
✗ ReqGuard v1 supports Python/pytest projects only.
  Detected: Go (go.mod). Language support is tracked in ROADMAP.md.
```

Honest scope beats a broken generality claim.

---

## 5. Repository Structure

```
reqguard/
├── pyproject.toml
├── README.md
├── CHANGELOG.md                    # the v0→v6 experiment log (Doc 1 §28)
├── Dockerfile
├── docs/
│   ├── 01-product-spec.md
│   └── 02-engineering-spec.md
│
├── src/reqguard/
│   ├── __init__.py
│   ├── cli.py                      # Typer app  (§14)
│   ├── config.py                   # reqguard.yaml → Config          (§11)
│   ├── models.py                   # every Pydantic model            (§6)
│   ├── errors.py
│   │
│   ├── agent/
│   │   ├── client.py               # Anthropic client + per-stage policy (§2)
│   │   ├── analyze.py              # Stage 1
│   │   ├── investigate.py          # Stage 2
│   │   ├── plan.py                 # Stage 3
│   │   ├── repair.py               # Stage 5
│   │   ├── baseline.py             # the control arm                 (§15)
│   │   └── prompts/
│   │       ├── analyze.md
│   │       ├── investigate.md
│   │       ├── plan.md
│   │       ├── repair.md
│   │       └── baseline.md
│   │
│   ├── tools/
│   │   ├── repo.py                 # pure functions — no LLM imports (§3.4)
│   │   └── registry.py             # @beta_tool bindings
│   │
│   ├── engine/
│   │   ├── sandbox.py              # isolation                       (§8)
│   │   ├── patch.py                # MutationSpec → diff, validated  (§7)
│   │   ├── runner.py               # pytest subprocess               (§9)
│   │   └── verify.py               # two-condition gate              (§10)
│   │
│   ├── trajectory.py               # JSONL event log                 (§12)
│   ├── evaluate.py                 # deterministic metrics           (§17)
│   └── report/
│       ├── render.py
│       └── templates/
│
├── benchmark/
│   ├── cases/
│   │   └── <case-id>/
│   │       ├── repo/               # a small, real, runnable project
│   │       ├── requirements.md
│   │       └── ground_truth.yaml
│   ├── manifest.yaml
│   └── validate.py                 # asserts every case is well-formed (§18.3)
│
└── tests/                          # ReqGuard's own tests
```

---

## 6. Core Data Model

Every inter-stage contract is a Pydantic model in `models.py`. Stages communicate only through these — no dicts crossing module boundaries.

```python
class Severity(str, Enum):
    HIGH = "high"; MEDIUM = "medium"; LOW = "low"

class RequirementCategory(str, Enum):
    AUTHZ = "auth_authorization"
    BUSINESS_RULE = "business_rule"
    STATE_TRANSITION = "state_transition"
    VALIDATION = "input_validation"
    ERROR_HANDLING = "api_error_handling"
    SEMANTIC = "semantic_domain"

class Requirement(BaseModel):
    """Stage 1 output. Doc 1 §9."""
    id: str
    text: str                       # verbatim from the source document
    invariant: str                  # "cancelled_order -> ship operation prohibited"
    prohibited_behavior: str        # what a violation looks like
    category: RequirementCategory
    severity: Severity
    source_location: str            # requirements.md:L42

class ImplementationSite(BaseModel):
    """Stage 2 output — where the requirement lives in code."""
    file: str
    symbol: str                     # OrderService.ship
    start_line: int
    end_line: int
    enforcement_snippet: str        # the lines that actually enforce it
    confidence: Literal["high", "medium", "low"]

class Investigation(BaseModel):
    requirement_id: str
    sites: list[ImplementationSite]
    covering_tests: list[str]       # test node ids believed to cover it
    domain_notes: str               # state model, terminology  (Doc 1 §8)

class MutationSpec(BaseModel):
    """Stage 3 output. Applied deterministically — never by the model."""
    id: str
    requirement_id: str
    file: str                       # repo-relative
    original_snippet: str           # must match EXACTLY ONCE in the file
    mutated_snippet: str
    rationale: str                  # why this violates the requirement
    violation_scenario: str         # concrete inputs that expose it
    expected_to_survive: bool | None = None   # agent's prediction, for calibration

class TestOutcome(BaseModel):
    passed: int; failed: int; errors: int; skipped: int
    exit_code: int
    duration_s: float
    failing_node_ids: list[str]
    stdout_tail: str

class MutationResult(BaseModel):
    mutation_id: str
    baseline: TestOutcome           # unmutated — MUST be green (§9.2)
    mutated: TestOutcome
    survived: bool                  # computed, never asserted by an LLM

class RepairProposal(BaseModel):
    requirement_id: str
    mutation_id: str
    test_path: str
    test_source: str
    explanation: str

class Verification(BaseModel):
    passes_on_original: bool        # condition A  (§10)
    kills_mutant: bool              # condition B  (§10)
    verified: bool                  # A and B
    failure_reason: str | None

class RequirementVerdict(BaseModel):
    requirement: Requirement
    status: Literal["PROTECTED", "INSUFFICIENTLY_PROTECTED",
                    "INCONCLUSIVE", "REPAIRED"]
    mutation: MutationSpec | None
    result: MutationResult | None
    repair: RepairProposal | None
    verification: Verification | None
```

`INCONCLUSIVE` is a first-class status, not an error path. See §9.2 and §19.

---

## 7. The Mutation Mechanism

### 7.1 The approach **[DECISION]**

The model emits a **typed `MutationSpec`**. A deterministic patcher applies it by exact anchor match.

Rejected:
- **LLM rewrites the whole file.** Unauditable, non-reproducible, and it will quietly reformat unrelated code — which contaminates the result.
- **A fixed AST operator catalogue** (`>=` → `>`, delete `if`, invert boolean). This is classic mutation testing, and Doc 1 §5 explicitly says that is *not* the product. A catalogue cannot express *"report a partially-fulfilled order as completed."*

The chosen form keeps the model's semantic freedom while keeping the *application* of the mutation mechanical, reversible, and diffable.

### 7.2 Extraction

Stage 3 uses structured output — the schema is the contract:

```python
response = client.messages.parse(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive", "display": "summarized"},
    output_config={"effort": "xhigh"},
    system=[{"type": "text", "text": PLAN_SYSTEM,
             "cache_control": {"type": "ephemeral"}}],
    messages=[{"role": "user", "content": render_plan_context(req, investigation)}],
    output_format=MutationSpec,
)
spec = response.parsed_output
```

### 7.3 Application

```python
def apply(spec: MutationSpec, sandbox: Path) -> Diff:
    target = safe_resolve(sandbox, spec.file)        # §8.3 path containment
    source = target.read_text(encoding="utf-8")

    occurrences = source.count(spec.original_snippet)
    if occurrences == 0:
        raise MutationRejected("anchor not found")
    if occurrences > 1:
        raise MutationRejected(f"anchor ambiguous ({occurrences} matches)")

    target.write_text(source.replace(spec.original_snippet, spec.mutated_snippet),
                      encoding="utf-8")
    return unified_diff(source, target.read_text())
```

Exact-match-exactly-once is the whole safety story. An ambiguous anchor is **rejected, not guessed** — the planner is re-prompted once with the failure, then the requirement is marked `INCONCLUSIVE`.

### 7.4 Validation gates — all must pass before the mutant is run

| # | Gate | Rejects |
|---|---|---|
| 1 | Anchor matches exactly once | ambiguous / hallucinated anchors |
| 2 | `ast.parse(mutated_source)` succeeds | syntactically broken mutants |
| 3 | Target is **not** a test file (`test_*.py`, `*_test.py`, `tests/**`, `conftest.py`) | **cheating — see §7.5** |
| 4 | Target resolves inside the sandbox root | path traversal |
| 5 | Diff ≤ `max_diff_lines` (default 15) | "mutations" that rewrite a module |
| 6 | `mutated_snippet != original_snippet` | no-op mutations |

### 7.5 The rule that protects the entire result

> **A mutation may never modify a test file.**

If the mutation engine can touch tests, "the tests didn't catch it" becomes trivially achievable and the headline metric is worthless. Gate 3 is enforced in `patch.py`, unit-tested in `tests/test_patch_rejects_test_files.py`, and asserted again in the evaluator against the recorded diff. Belt, braces, and a third check at scoring time.

### 7.6 Equivalent mutants — the honest hard problem

A mutation can survive because it is *semantically identical* to the original. Classic mutation testing has no general solution to this, and pretending otherwise would be the single easiest way to inflate our numbers.

ReqGuard's answer falls out of the architecture: **the repair test is the non-equivalence witness.** If Stage 5 produces a test that passes on the original and fails on the mutant, then the mutant is provably *not* equivalent — the test is the distinguishing input. If no such test can be produced, we do not claim a gap:

```
mutation survived
      ↓
repair test passes on original AND fails on mutant?
      ├── YES → genuine requirement gap, and it is now closed
      └── NO  → INCONCLUSIVE  (possible equivalent mutant)
                 reported honestly, counted against us in precision
```

This is the most defensible property of the design and should be said out loud in the demo.

---

## 8. Sandbox

### 8.1 Strategy **[DECISION]**

Default **copy-tree**; `git worktree` opt-in.

```yaml
sandbox:
  strategy: copy        # copy | worktree
```

`git worktree` is faster and cheaper, but checks out only tracked files — no `.venv`, no untracked fixtures, no generated data. For an arbitrary user repo that silently breaks the test suite, and a broken suite reads as "mutation killed", which is a **false negative that looks like success**. Copy is the safe default; worktree is available for large, clean repos where the speed matters.

Exclusions on copy: `.git`, `__pycache__`, `.venv`, `node_modules`, `.tox`, `.mypy_cache`, `.pytest_cache`.

### 8.2 Lifecycle

```
tempfile.mkdtemp(prefix="reqguard-")
      │
      ├── copy repo
      ├── run baseline suite         ← must be green (§9.2)
      ├── apply mutation
      ├── run suite                  ← survival verdict
      ├── write repair test
      ├── run repair on ORIGINAL     ← condition A
      ├── run repair on MUTANT       ← condition B
      │
      └── shutil.rmtree()            ← in a finally: block, always
```

One sandbox per requirement. Reused across the mutate/repair/verify steps for that requirement so the baseline run is paid once.

### 8.3 Containment

- Every path from the model goes through `safe_resolve()`: resolve, then assert `sandbox_root in path.parents`. Rejects `../`, symlink escapes, and absolute paths.
- The user's repository is opened **read-only** throughout. The only write path into it is `reqguard apply`, which requires explicit confirmation (§14.3).
- Test subprocess: `cwd=sandbox`, hard timeout (default 300s), `SIGKILL` on the process group.

### 8.4 Docker **[DECISION]**

Docker is **not** the default sandbox. It is:
- the reproducible environment for **benchmark runs** (`docker run reqguard bench run`) — this is what makes the submission reproducible;
- an opt-in isolation level (`sandbox.docker: true`) for untrusted repositories.

Making Docker mandatory for normal use would break the `reqguard analyze .` experience on a developer's own machine, where their venv already exists and works.

We are executing a third party's test suite. On the user's own repo that is the same trust level as running `pytest` themselves — stated plainly in the README rather than hidden.

---

## 9. Test Runner

### 9.1 Mechanism

`subprocess.run` of the configured `test_command` with `--json-report` (pytest-json-report) for structured node-level results, falling back to exit-code-only parsing if the plugin is unavailable in the target environment.

```
exit 0  → all passed
exit 1  → tests failed        ← the signal we care about
exit 2  → interrupted
exit 3  → internal error      ← INCONCLUSIVE, not "killed"
exit 4  → usage error         ← INCONCLUSIVE, not "killed"
exit 5  → no tests collected  ← INCONCLUSIVE, not "killed"
timeout → INCONCLUSIVE
```

Only exit codes 0 and 1 produce a survival verdict. **Everything else is `INCONCLUSIVE`.** Treating a collection error as "mutation killed" would silently inflate the kill rate — this is the most likely way for the system to lie to us, so it gets its own explicit branch and its own unit test.

### 9.2 The baseline-green precondition

Before any mutation is applied, the unmutated suite must pass.

```python
if not baseline.exit_code == 0:
    return RequirementVerdict(status="INCONCLUSIVE",
        reason="test suite is not green before mutation; results would be meaningless")
```

A red suite makes every downstream signal uninterpretable. Doc 1's entire premise is *"the tests pass — and yet."*

### 9.3 Survival

```python
survived = (mutated.exit_code == 0)
```

That is the whole verdict function. It is four tokens of logic and no model is involved in producing it (Doc 1 §12, §14).

---

## 10. The Verification Gate

Doc 1 §14 requires that the agent cannot self-certify. Concretely:

```python
def verify(repair, original_sandbox, mutant_sandbox) -> Verification:
    a = run_single_test(original_sandbox, repair)   # must PASS
    b = run_single_test(mutant_sandbox,  repair)    # must FAIL

    return Verification(
        passes_on_original = (a.exit_code == 0),
        kills_mutant       = (b.exit_code == 1),
        verified           = (a.exit_code == 0 and b.exit_code == 1),
        failure_reason     = _diagnose(a, b),
    )
```

**Both conditions are required.**

- Condition B alone is trivially gamed by a test that always fails (`assert False`). It would "kill" every mutant and repair nothing.
- Condition A alone proves only that the test is valid, not that it detects anything.

Together they prove the test is a genuine discriminator — and, per §7.6, that the mutant is not equivalent.

```
        │  passes on original  │  fails on mutant  │  verdict
────────┼──────────────────────┼───────────────────┼──────────────
   A∧B  │         ✓            │        ✓          │  REPAIRED ✓
   ¬A∧B │         ✗            │        ✓          │  rejected (broken test)
   A∧¬B │         ✓            │        ✗          │  rejected (doesn't detect)
   ¬A∧¬B│         ✗            │        ✗          │  rejected
```

Rejected repairs get one retry with the failure reason fed back, then the requirement is reported as `INSUFFICIENTLY_PROTECTED` with no repair — an honest partial result, not a silent success.

---

## 11. Configuration

`reqguard.yaml`, discovered upward from the target path. Every field has a default; a repo with no config file works.

```yaml
language: python                 # v1: python only
test_command: pytest             # runs in the sandbox

requirements:
  - requirements.md              # files or globs

source_paths: [src/]             # mutation targets are restricted to these
exclude: ["**/migrations/**"]

rules:                           # per-category severity floor (Doc 1 §18)
  auth_authorization: high
  business_rule: high
  state_transition: high
  input_validation: medium
  api_error_handling: medium
  semantic_domain: high

mutation_limits:
  max_mutations: 20
  max_diff_lines: 15
  attempts_per_requirement: 1    # ≥2 = retry a rejected mutation

sandbox:
  strategy: copy                 # copy | worktree
  docker: false
  test_timeout_s: 300

model:
  id: claude-opus-5
  effort:
    analyze: medium
    investigate: high
    plan: xhigh
    repair: high

report:
  output_dir: ./reqguard-report
```

`source_paths` matters: it is a second, user-controlled guard on what the mutation engine may touch, layered on top of §7.4's test-file gate.

Project-specific context (Doc 1 §18) is supplied via an optional `REQGUARD.md` at the repo root, appended to the investigation stage's user turn — **not** to the cached system prefix, so it never invalidates the cache.

---

## 12. Trajectory Logger

Append-only JSONL at `reqguard-report/trajectory.jsonl`. One event per step. This is simultaneously the submission's "representative trajectory" artifact (Doc 1 §27), the cost meter, and the debugger.

```json
{"ts":"2026-08-29T10:14:02.881Z","run_id":"r_8f2a","requirement_id":"REQ-07",
 "stage":"plan","type":"thinking","summary":"The ship() guard is the only enforcement point..."}
{"ts":"...","stage":"plan","type":"api_call","model":"claude-opus-5",
 "request_id":"req_018Ee...","usage":{"input_tokens":12481,"output_tokens":903,
 "cache_read_input_tokens":11200}}
{"ts":"...","stage":"execute","type":"tool_result","tool":"run_tests",
 "payload":{"exit_code":0,"passed":42,"failed":0}}
{"ts":"...","stage":"execute","type":"verdict","payload":{"survived":true}}
```

Event types: `thinking` · `api_call` · `tool_call` · `tool_result` · `decision` · `verdict` · `error`.

Every `api_call` records `response._request_id` and the full `usage` object. Cost in the final report is **summed from these records**, never estimated from a price table — the report says what was actually spent.

`reqguard trajectory --format md <run_id>` renders the reasoning → tool → observation → adaptation → verification arc that Doc 1 §27 describes, ready to paste into the submission.

---

## 13. Reporting

Two outputs from one `RunResult`:

1. **Terminal** — Rich-rendered, the format in Doc 1 §15/§16.
2. **`reqguard-report/`**
   ```
   report.md              human-readable, per requirement
   results.json           machine-readable, the evaluator's input
   trajectory.jsonl       §12
   mutations/REQ-07.diff  the exact applied diff, reproducible
   repairs/REQ-07.py      the proposed test, unapplied
   ```

Repairs are **written to the report directory, never to the user's repo** (Doc 1 §19). Applying them is a separate, explicit command (§14.3).

---

## 14. CLI

Typer + Rich.

### 14.1 Commands

```bash
reqguard analyze .                          # full pipeline, default requirement discovery
reqguard check --requirements reqs.md .     # explicit requirements file
reqguard analyze . --requirement REQ-07     # single requirement (the demo path)
reqguard analyze . --dry-run                # stages 1-3 only; plan, don't execute

reqguard apply --requirement REQ-07         # write a verified repair into the repo (§14.3)

reqguard bench run    --arm reqguard|baseline|both
reqguard bench eval                         # deterministic scoring (§17)
reqguard trajectory <run_id> --format md
```

### 14.2 Progress output

Matches Doc 1 §16 exactly — it is a specified interface, not decoration:

```
Analyzing requirements...       ✓  15 found
Mapping implementation...       ✓
Inspecting tests...             ✓
Generating mutations...         ✓  15 planned
Running verification...         ✓  15 executed

Requirements analyzed: 15
Protected:              11
Potential gaps:          4
Inconclusive:            0

High-risk gaps:          2
Medium-risk gaps:        2

Report: ./reqguard-report/
```

`Inconclusive` is shown even when zero. Hiding it when it's zero trains the user not to look for it when it isn't.

### 14.3 The only write path into the user's repo

```
$ reqguard apply --requirement REQ-07

Will write:  tests/test_orders_cancelled.py  (new file, 14 lines)

  ✓ verified: passes on current code
  ✓ verified: fails when the cancellation guard is removed

Proceed? [y/N]
```

Refuses to run if the repair is not `verified` (§10). Refuses to overwrite an existing file. Never invoked automatically by `analyze`.

---

## 15. The Baseline Arm

### 15.1 Design

`agent/baseline.py`. Same model, `xhigh` effort, the **same read-only tool surface** (`list_files`, `read_file`, `search_code`, `run_tests`) via the same Tool Runner. One prompt:

> Here is a requirement and a repository. Determine whether the existing test suite adequately protects this requirement. Answer PROTECTED or INSUFFICIENTLY_PROTECTED, with your reasoning.

Its structured output is a verdict per requirement, scored by the same evaluator against the same ground truth.

This is a fair, strong baseline: it is what a competent developer would actually do with a coding agent today, which is exactly the comparison Doc 1 §25 asks for.

### 15.2 A second baseline, if time allows

`baseline-agent-sdk` — the Claude Agent SDK (a general-purpose coding agent with built-in file and bash tools) given the same instruction. Strictly optional; only run it if the primary arms are done and stable. It answers the sharper judge question: *does a fully general coding agent already solve this?*

---

## 16. Dependencies and Packaging

```toml
[project]
name = "reqguard"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=1.0",        # Tool Runner, messages.parse, structured outputs
    "pydantic>=2.7",         # every inter-stage contract
    "typer>=0.12",           # CLI
    "rich>=13.7",            # terminal report
    "pyyaml>=6.0",           # config + ground truth
]

[project.optional-dependencies]
dev  = ["pytest", "pytest-json-report", "ruff", "mypy"]
```

Deliberately absent:
- **libcst / astroid** — `ast.parse` from the stdlib is sufficient for gate 2 (§7.4). We validate syntax; we do not rewrite trees.
- **tenacity** — the Anthropic SDK already retries 429/5xx/connection errors with backoff.
- **mutmut / cosmic-ray** — off-the-shelf mutation testing is exactly what Doc 1 §5 says we are not. Their operators are syntactic; ours are requirement-derived.
- **LangChain and friends** — no orchestration layer is needed for six stages with a typed contract between each.

Environment: `uv` for installation and lockfile. Docker image pinned to `python:3.11-slim` for benchmark reproducibility.

**API key handling:** `anthropic.Anthropic()` with no arguments. It resolves `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then an `ant auth login` profile. Never hardcode, never prompt interactively, never log the key. A missing credential fails with a one-line message pointing at both options.

---

## 17. Evaluator

`evaluate.py`. **Contains no LLM call of any kind.** It reads `results.json` from each arm plus `ground_truth.yaml` per case and computes:

**Primary — Requirement Mutation Kill Rate** (Doc 1 §23):

```
kill_rate = mutations_killed / mutations_generated
```

**Secondary** (Doc 1 §24):

| Metric | Definition |
|---|---|
| Precision | correctly-reported gaps / all reported gaps |
| Recall | correctly-reported gaps / ground-truth gaps |
| Clean-case FP rate | gaps reported on clean cases / clean-case requirements |
| Repair success rate | verified repairs / attempted repairs (§10 gate) |
| Inconclusive rate | inconclusive / total — reported, never buried |
| Cost | summed from trajectory `usage`, not estimated |
| Runtime | wall clock per case |

Output: `benchmark/results/<timestamp>/{results.json, comparison.md}` with a baseline-vs-ReqGuard table and per-category breakdown.

**The evaluator is deterministic and re-runnable against stored artifacts.** Re-scoring never requires re-spending money, and two people scoring the same run get the same number.

### 17.1 A rule about numbers

No result appears in the README, the report, or the demo unless it was produced by `reqguard bench eval` from committed artifacts. Placeholder values in this document and in Doc 1 (`XX%`, `YY%`) stay as placeholders until a real run replaces them.

---

## 18. Benchmark

### 18.1 Case structure

```
benchmark/cases/07-cancelled-order-shipping/
├── repo/
│   ├── src/orders/service.py
│   ├── tests/test_orders.py
│   └── pyproject.toml
├── requirements.md
└── ground_truth.yaml
```

```yaml
# ground_truth.yaml
case_id: "07-cancelled-order-shipping"
category: business_rule
difficulty: medium
requirements:
  - id: REQ-07
    text: "Cancelled orders cannot be shipped."
    severity: high
    protected: false            # THE ground truth label
    rationale: >
      The guard exists in ship(), but the suite only covers the happy path.
      No test constructs a cancelled order and asserts the rejection.
    expected_mutation: "remove the order.status == 'cancelled' guard in ship()"
    must_survive: true
```

### 18.2 Composition — 20 cases

| Category | Cases | Of which clean |
|---|---|---|
| Auth / authorization | 3 | 1 |
| Business rules | 3 | 1 |
| State transitions | 3 | 1 |
| Input validation | 3 | 1 |
| API / error handling | 3 | 1 |
| Semantic / domain | 5 | 1 |
| **Total** | **20** | **6** |

Six clean cases — 30% — is deliberate. Doc 1 §22 makes false-positive rate a headline concern, and a benchmark where every case has a defect rewards an agent that always cries wolf.

### 18.3 `benchmark/validate.py`

Runs in CI, no API calls. For every case, asserts:

1. `repo/` test suite is **green** on unmutated code (§9.2 would otherwise void the case)
2. `ground_truth.yaml` parses and every requirement has an explicit `protected` label
3. Every `protected: false` case has a stated `rationale`
4. Clean cases have no deliberately-introduced defect
5. Case ids are unique and match directory names

A silently-broken benchmark case produces a plausible-looking wrong number. This script is cheap insurance.

### 18.4 The killer case

`benchmark/cases/13-partial-fulfillment/` — Doc 1 §29, verbatim.

```python
# src/orders/fulfillment.py
if shipped_quantity >= 0:          # the latent defect
    status = "completed"

# tests/test_fulfillment.py covers 0 shipped and 100 shipped.
# Nothing covers 80 of 100.
```

Ground truth: `protected: false`, `difficulty: hard`. Expected outcome — the baseline says PROTECTED (the guard *looks* deliberate and the tests *do* pass), ReqGuard mutates `80/100 → COMPLETED`, the suite passes, the mutation survives, and the generated partial-fulfillment test kills it.

If ReqGuard cannot beat the baseline on this case, the central hypothesis (Doc 1 §35) is in trouble — and we report that, because that is what §17.1 commits us to.

---

## 19. Failure Modes We Design Against

| Failure | Why it matters | Mitigation |
|---|---|---|
| Mutation edits a test file | Makes the headline metric meaningless | §7.4 gate 3, unit-tested, re-checked at scoring |
| Suite already red | Every downstream signal is noise | §9.2 baseline precondition |
| Collection error read as "killed" | Silently inflates kill rate | §9.1 — only exit 0/1 give a verdict |
| Equivalent mutant reported as a gap | Inflates gap count, wastes user time | §7.6 witness requirement → `INCONCLUSIVE` |
| Repair test that always fails | "Kills" every mutant, repairs nothing | §10 condition A |
| Repair test that tests nothing | Looks green, protects nothing | §10 condition B |
| Agent asserts its own success | Doc 1 §14's central concern | §3.2 — no LLM in stages 4, 6, 7 |
| Handicapped baseline | Invalidates the whole experiment | §2.3 — same model, highest effort |
| Cache silently invalidated | 5× cost, discovered at the bill | §2.5 CI assertion |
| Numbers drift from artifacts | Unreproducible submission | §17.1 — evaluator output only |

---

## 20. Build Sequence

Mapped to Doc 1 §28's changelog so that each step is a measurable experiment, not just a task.

### Day 1 — Skeleton and the deterministic half

| # | Work | Gate |
|---|---|---|
| 1 | `pyproject.toml`, package skeleton, `models.py`, `errors.py` | imports clean |
| 2 | `engine/sandbox.py` — copy, containment, teardown | unit tests pass |
| 3 | `engine/runner.py` — pytest subprocess, exit-code taxonomy | unit tests incl. exit 5 |
| 4 | `engine/patch.py` — apply + all six gates (§7.4) | **test-file gate test passes** |
| 5 | `trajectory.py` | JSONL round-trips |
| 6 | 3 benchmark cases + `validate.py` | validator green |

**Everything on Day 1 is testable without an API key.** The deterministic core is built first precisely because it is what makes the LLM's output trustworthy.

### Day 2 — The agent, then the experiment

| # | Work | Changelog | Gate |
|---|---|---|---|
| 7 | `agent/client.py` + per-stage policy | — | one live call succeeds |
| 8 | Stage 1 Analyzer (`messages.parse`) | **v1** | requirements extracted on 3 cases |
| 9 | Stage 2 Investigator (Tool Runner + read tools) | **v1** | sites located |
| 10 | Stage 3 Planner + Stage 4 Executor | **v2** | end-to-end survival verdict |
| 11 | `agent/baseline.py` | **v0** | baseline verdicts on 3 cases |
| 12 | `evaluate.py` | — | first real kill-rate number |
| 13 | Cases → 20 | — | validator green |

By the end of Day 2 the **experiment runs end to end**, even if the numbers are bad. A working experiment on Day 2 beats a better agent on Day 3 with nothing to measure it against.

### Day 3 — Repair, verification, submission

| # | Work | Changelog | Gate |
|---|---|---|---|
| 14 | Stage 5 Repair (Tool Runner + `submit_test`) | **v4** | test generated |
| 15 | Stage 6 Verification, two-condition gate | **v5** | `verified` on the killer case |
| 16 | `report/render.py`, `reqguard apply` | — | matches Doc 1 §15 |
| 17 | Dockerfile, README, reproduction steps | — | clean-machine run works |
| 18 | Full benchmark, both arms | — | `comparison.md` |
| 19 | Ablation: drop one component, re-measure | **v6** | recorded either way |
| 20 | Trajectory export, demo rehearsal | — | ≤5 min |

### Cut list, in order

If Day 3 runs short, drop in this order — the earlier items cost the least:

1. Second baseline (§15.2)
2. Ablation study (step 19) — *report that it wasn't run*
3. `worktree` sandbox strategy
4. Docker (keep a documented local repro path)
5. Semantic cases 4–5 (keep the killer case; it is the demo)

**Never cut:** the verification gate (§10), the test-file gate (§7.5), the clean cases (§18.2), or evaluator-sourced numbers (§17.1). Those four are what make the result mean anything.

---

## 21. Definition of Done — Traceability

Doc 1 §33's checklist, mapped to where it is satisfied.

| Doc 1 requirement | Where |
|---|---|
| reads a repository | §5 `tools/repo.py` |
| understands requirements | §6 Stage 1 |
| finds relevant implementation | §6 Stage 2 |
| generates meaningful violations | §7 Stage 3 |
| runs tests | §9 |
| detects surviving mutations | §9.3 |
| generates / repairs tests | §10 Stage 5 |
| verifies the repair | §10 two-condition gate |
| actionable report | §13 |
| baseline exists | §15 |
| benchmark ≥10 cases, ground truth | §18 (20 cases) |
| difficult case exists | §18.4 |
| clean cases exist | §18.2 (6) |
| same cases, both arms | §15, §17 |
| primary + secondary metrics | §17 |
| isolated mutation environment | §8 |
| no unauthorized repo modification | §8.3, §14.3 |
| reproducible installation | §16 |
| deterministic evaluator | §17 |
| trajectory logging | §12 |
| README / changelog / repro / results | §20 steps 17–20 |

---

## 22. Open Questions

Flagged rather than silently decided. None block Day 1.

1. **Requirement discovery when no requirements file exists.** Extract from docstrings and README? Or require `--requirements`? *Leaning: require it in v1; auto-discovery is a v2 feature with its own error modes.*
2. **Multiple mutations per requirement.** `attempts_per_requirement` exists in config but defaults to 1. Does 3 mutations per requirement raise recall enough to justify 3× cost? *Answerable empirically once §17 works — a good v3 experiment.*
3. **Requirements with no single enforcement site** (cross-cutting concerns: logging, rate limiting). `MutationSpec` assumes one anchor. *Likely `INCONCLUSIVE` in v1; worth one benchmark case to characterise the failure.*
4. **Repair placement.** New test file vs. appending to an existing one. *Leaning: new file, `tests/test_reqguard_<req_id>.py` — no merge conflicts, trivially reviewable, trivially deletable.*

---

*End of Document 2. The next artifact is code, not prose.*
