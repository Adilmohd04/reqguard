# ReqGuard — Implementation Manual

**Document 3: THE BUILD**
**Version:** 1.0
**Goal:** The file-by-file, prompt-by-prompt, command-by-command manual. You should be able to type from this.

---

## 0. How This Document Relates to Docs 1 and 2

| Doc | Question | Nature |
|---|---|---|
| 1 — Product Specification | **WHAT** we build | Product definition |
| 2 — Engineering Blueprint | **HOW** we build it | Architecture, stack, priorities |
| **3 — Implementation Manual** | **WHAT EXACTLY TO TYPE** | Code, schemas, prompts, gates |

Doc 2 §41 closes with a standing instruction:

> before implementing the exact agent SDK/MCP APIs, we should verify their **current August 2026 official documentation and versions**

**That item is discharged in §1 of this document.** Every API shape below was checked against the current Anthropic Python SDK reference, not recalled.

### 0.1 The two-Document-2 situation

An earlier draft, `docs/02-engineering-spec.md`, also claimed the Document 2 slot. `02-engineering-blueprint.md` (this series' Doc 2) is **canonical**. The earlier draft is retained as a decision record; where the two disagree, §21 lists every conflict and this manual implements the blueprint's position unless stated otherwise.

Three of the blueprint's positions **override** the earlier draft and are adopted here:

1. **Constrained mutation catalogue** (blueprint §11) over free-form mutation. Reconciled in §6 — catalogue plus one escape hatch.
2. **No hidden chain-of-thought in trajectories** (blueprint §29). This kills the earlier plan to log summarized thinking. Implemented in §11.
3. **`summary.html` in the report** (blueprint §30). Implemented in §12.

---

## 1. Verified Platform Decisions

Checked against the current Anthropic Python SDK reference this session. Pin these; re-verify only if a call 400s.

| Concern | Decision | Verified detail |
|---|---|---|
| Model | `claude-opus-5` | 1M context, $5/$25 per MTok |
| Agent runtime | Anthropic Python SDK **Tool Runner** | `client.beta.messages.tool_runner(...)` — beta; SDK drives the loop, we define tools, we host compute |
| Structured stages | `client.messages.parse(..., output_format=PydanticModel)` | returns `.parsed_output`, validated |
| Thinking | `thinking={"type": "adaptive"}` | on by default on Opus 5; `budget_tokens` **removed** — sending it returns 400 |
| Thinking display | **`omitted`** (the default) | deliberate — see §11, blueprint §29 |
| Effort | `output_config={"effort": ...}` | `low`\|`medium`\|`high`\|`xhigh`\|`max`; nested inside `output_config`, not top-level |
| Prefill | **Not available** | assistant-turn prefill returns 400 on Opus 5 |
| Caching | `cache_control={"type": "ephemeral"}` on the system block | prefix match — any byte change invalidates everything after |
| Client | `anthropic.Anthropic()` zero-arg | resolves `ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → `ant auth login` profile |
| Retries | built in (2, exponential backoff, 429/5xx/connection) | **do not add `tenacity`** |
| SDK version | `anthropic>=1.0` | 1.x is built on `httpx2`, not `httpx` |

Two traps worth stating because they are silent:

- **`budget_tokens` is a 400 on Opus 5.** If you carry a snippet forward from an older codebase, this is where it breaks.
- **The Python Tool Runner does not auto-resume `stop_reason: "pause_turn"`.** It exits the loop and returns a truncated answer with no error. `pause_turn` only arises from *server-side* tools and we declare none — so we assert on it rather than handle it (§10.3).

### 1.1 Model policy per stage

```python
# src/reqguard/agent/policy.py
EFFORT = {
    "analyze":     "medium",   # structured extraction from prose
    "investigate": "high",     # multi-file correlation
    "plan":        "xhigh",    # the hardest reasoning in the system
    "repair":      "high",     # constrained by investigation already in context
    "baseline":    "xhigh",    # DELIBERATELY the highest — see §16.2
}
```

---

## 2. Day 0 — Bootstrap (20 minutes)

```bash
mkdir -p reqguard && cd reqguard
git init
uv venv --python 3.12
uv pip install anthropic pydantic typer rich pyyaml jinja2
uv pip install --dev pytest pytest-json-report ruff mypy
```

`pyproject.toml`:

```toml
[project]
name = "reqguard"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "anthropic>=1.0",
    "pydantic>=2.7",
    "typer>=0.12",
    "rich>=13.7",
    "pyyaml>=6.0",
    "jinja2>=3.1",      # summary.html only
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-json-report>=1.5", "ruff", "mypy"]

[project.scripts]
reqguard = "reqguard.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Deliberately absent, with reasons:

| Not used | Why |
|---|---|
| `tenacity` | SDK already retries 429/5xx/connection with backoff |
| `libcst` / `astroid` | stdlib `ast.parse` is enough to validate a mutant parses |
| `mutmut` / `cosmic-ray` | their operators are syntactic; ours are requirement-derived (Doc 1 §5) |
| LangChain et al. | six stages with typed contracts need no orchestration layer |

```bash
uv pip install -e .
reqguard --help    # gate: exits 0
```

---

## 3. Data Contracts

`src/reqguard/models.py`. Every stage boundary is one of these. No raw dicts cross a module boundary.

```python
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


class Severity(str, Enum):
    HIGH = "high"; MEDIUM = "medium"; LOW = "low"


class Category(str, Enum):
    AUTHORIZATION    = "authorization"
    STATE_TRANSITION = "state_transition"
    BUSINESS_RULE    = "business_rule"
    VALIDATION       = "validation"
    API_ERROR        = "api_error"
    SEMANTIC         = "semantic"


# ── Stage 1 ───────────────────────────────────────────────
class Behavior(BaseModel):
    precondition: str          # "order.status == cancelled"
    prohibited_action: str     # "ship(order)"
    expected_result: str       # "operation rejected"


class Requirement(BaseModel):
    id: str                    # REQ-007
    statement: str             # verbatim from the source document
    behavior: Behavior
    category: Category
    severity: Severity
    source_location: str       # requirements.md:L42


# ── Stage 2 ───────────────────────────────────────────────
class Site(BaseModel):
    file: str
    symbol: str                # "OrderService.ship_order"
    start_line: int
    end_line: int
    enforcement_snippet: str   # the lines that actually enforce the requirement
    confidence: Literal["high", "medium", "low"]


class Investigation(BaseModel):
    requirement_id: str
    sites: list[Site]
    covering_tests: list[str]  # pytest node ids believed to cover it
    domain_notes: str          # state model / terminology  (Doc 1 §8)
    rationale: str             # ONE line, for the trajectory  (§11)


# ── Stage 3 ───────────────────────────────────────────────
class MutationType(str, Enum):
    REMOVE_GUARD         = "remove_guard"           # blueprint §11
    COMPARISON_CHANGE    = "comparison_change"
    BOOLEAN_INVERSION    = "boolean_inversion"
    BOUNDARY_SHIFT       = "boundary_shift"
    ALLOW_TRANSITION     = "allow_transition"
    REMOVE_VALIDATION    = "remove_validation"
    SEMANTIC_CHANGE      = "semantic_change"        # escape hatch — §6.2


class MutationSpec(BaseModel):
    id: str
    requirement_id: str
    mutation_type: MutationType
    target_file: str                 # repo-relative
    target_symbol: str
    original_snippet: str            # MUST match exactly once  (§6.3)
    mutated_snippet: str
    description: str                 # "Remove cancelled-order validation"
    expected_violation: str          # "Cancelled order can be shipped"
    violation_scenario: str          # concrete inputs that expose it
    rationale: str                   # ONE line, for the trajectory


# ── Stage 4 / execution ───────────────────────────────────
class RunStatus(str, Enum):
    OK                = "ok"
    ENVIRONMENT_ERROR = "environment_error"    # blueprint §33
    AGENT_ERROR       = "agent_error"
    TIMEOUT           = "timeout"


class TestOutcome(BaseModel):
    status: RunStatus
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    failing_node_ids: list[str] = Field(default_factory=list)
    stdout_tail: str = ""
    reason: str | None = None        # populated when status != OK


class MutationResult(BaseModel):
    mutation_id: str
    baseline: TestOutcome            # unmutated — MUST be green  (§8.3)
    mutated: TestOutcome
    diff: str
    survived: bool | None            # None when inconclusive


# ── Stage 5 / 6 ───────────────────────────────────────────
class RepairProposal(BaseModel):
    requirement_id: str
    mutation_id: str
    test_path: str
    test_source: str
    explanation: str
    rationale: str


class Verification(BaseModel):
    passes_on_original: bool         # condition A  (§9)
    kills_mutant: bool               # condition B  (§9)
    verified: bool
    failure_reason: str | None = None


class Verdict(BaseModel):
    requirement: Requirement
    status: Literal["PROTECTED", "INSUFFICIENTLY_PROTECTED",
                    "REPAIRED", "INCONCLUSIVE"]
    investigation: Investigation | None = None
    mutation: MutationSpec | None = None
    result: MutationResult | None = None
    repair: RepairProposal | None = None
    verification: Verification | None = None
    inconclusive_reason: str | None = None
```

`INCONCLUSIVE` is a first-class outcome, not an error path. Blueprint §33 requires that an environment failure never be scored as a mutation kill; this is the type that carries it.

---

## 4. Tool Layer

Blueprint §8 names seven tools. Implement them as **pure functions with no Anthropic imports** — that is what makes the MCP adapter (blueprint §18) a thin binding rather than a rewrite.

```
src/reqguard/tools/repository.py      ← pure functions, zero LLM dependency
        ├── src/reqguard/tools/registry.py    (@beta_tool bindings)     v1
        └── src/reqguard/mcp/server.py        (MCP bindings)            P2
```

### 4.1 The seven tools

```python
# src/reqguard/tools/repository.py
from pathlib import Path
import ast, subprocess

def list_files(root: Path, glob: str = "**/*.py", limit: int = 200) -> list[str]:
    """Repo-relative paths matching a glob."""

def read_file(root: Path, path: str, start: int = 1, end: int | None = None) -> str:
    """File contents with 1-indexed line numbers prefixed."""

def search_code(root: Path, pattern: str, glob: str = "**/*.py",
                limit: int = 50) -> list[dict]:
    """Regex search. Returns [{file, line, text}]."""

def find_symbol(root: Path, name: str) -> list[dict]:
    """AST walk for a function/class definition.
    Returns [{file, symbol, start_line, end_line, source}]."""

def find_references(root: Path, name: str) -> list[dict]:
    """Where a symbol is used. Word-boundary regex — NOT scope-aware.
    Documented as approximate; that limitation is stated to the model."""

def inspect_tests(root: Path) -> list[dict]:
    """pytest --collect-only -q. Returns [{node_id, file, name}]."""

def git_diff(root: Path, ref: str = "HEAD") -> str:
    """Empty string when the repo is not a git repo — never raises."""
```

`find_references` being regex-based rather than scope-aware is a real limitation. It is **stated in the tool's description string** so the model treats results as candidates, not ground truth. A tool that silently over-reports is worse than one that admits it.

### 4.2 Path containment — the guard everything else rests on

```python
# src/reqguard/verification/safety.py
def safe_resolve(root: Path, candidate: str) -> Path:
    """Resolve `candidate` under `root`, or raise. Blocks ../, symlink
    escapes, and absolute paths."""
    root = root.resolve()
    target = (root / candidate).resolve()
    if root != target and root not in target.parents:
        raise PathEscape(f"{candidate!r} resolves outside the sandbox")
    return target
```

Every tool and every mutation call goes through this. It has its own unit test.

### 4.3 Binding to the Tool Runner

```python
# src/reqguard/tools/registry.py
from anthropic import beta_tool
from . import repository

def build_read_tools(root: Path) -> list:
    @beta_tool
    def search_code(pattern: str, glob: str = "**/*.py") -> str:
        """Search the repository with a regex.

        Args:
            pattern: Python regular expression.
            glob: Restrict to files matching this glob.
        """
        return json.dumps(repository.search_code(root, pattern, glob))

    # ... one binding per tool, emitted in a fixed order
    return [list_files, read_file, search_code, find_symbol,
            find_references, inspect_tests, git_diff]
```

Tools are returned in a **fixed, sorted order**. Tool definitions are part of the cached prefix; a set that reorders between calls silently invalidates the cache and quietly multiplies cost.

---

## 5. Agent Stages — The Actual Prompts

System prompts live in `src/reqguard/agent/prompts/*.md`, loaded once at import. **No interpolation into the system prompt** — no paths, no timestamps, no run ids. Volatile content goes in the user turn, after the cache breakpoint.

### 5.1 The shared call shape

```python
# src/reqguard/agent/client.py
import anthropic
from .policy import EFFORT

_client = anthropic.Anthropic()   # zero-arg: resolves env or ant profile

def structured(stage: str, system: str, user: str, output_format, trace):
    resp = _client.messages.parse(
        model="claude-opus-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},            # display defaults to omitted (§11)
        output_config={"effort": EFFORT[stage]},
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=output_format,
    )
    trace.api_call(stage, resp)                   # request_id + usage
    return resp.parsed_output
```

### 5.2 `prompts/analyze.md` — Stage 1

```markdown
You extract testable behavioral contracts from software requirements.

For each requirement in the supplied document, produce:
- a stable id (REQ-001, REQ-002, ...)
- the statement, VERBATIM from the source
- a behavior triple: precondition, prohibited_action, expected_result
- a category and a severity
- the source location as file:Lnn

Rules:
- Extract only requirements that constrain observable program behavior.
  Skip aspirations ("the system should be fast") that no test could falsify.
- The prohibited_action must be a concrete operation, not a description.
  "ship(order)" — not "shipping is not allowed".
- Do not invent requirements that are not in the document.
- If a sentence bundles several constraints, split it into several requirements.
```

### 5.3 `prompts/investigate.md` — Stage 2

```markdown
You locate where a requirement is enforced in a codebase.

You have read-only tools. Investigate progressively — do not dump the
repository. A good investigation is 5-15 tool calls.

Method:
1. Search for the domain nouns and verbs in the requirement.
2. Find the symbol that performs the prohibited action.
3. Read it. Identify the exact lines that enforce (or fail to enforce)
   the requirement.
4. Collect the tests that appear to cover that symbol.

Report:
- sites: every enforcement point, with the exact enforcement_snippet
- covering_tests: pytest node ids you believe exercise this requirement
- domain_notes: the state model and terminology a newcomer would need
- rationale: ONE sentence naming what you selected and why

Honesty rules:
- If the requirement has no enforcement point in the code, say so with an
  empty sites list. Do not invent one.
- find_references is a regex search, not scope-aware. Treat its results as
  candidates to verify by reading, not as facts.
- Mark confidence low when you are inferring rather than reading.
```

### 5.4 `prompts/plan.md` — Stage 3

This is the highest-leverage prompt in the system. It runs at `xhigh` effort.

```markdown
You design a MINIMAL, PLAUSIBLE code change that violates a stated
requirement — the change a competent developer might make by accident.

You are given a requirement, the enforcement sites, and the existing tests.

Choose a mutation_type:
  remove_guard        delete a conditional that blocks a prohibited action
  comparison_change   <= becomes <, == becomes !=, etc.
  boolean_inversion   invert a condition
  boundary_shift      >= becomes >, off-by-one at a boundary
  allow_transition    permit a state transition that must be forbidden
  remove_validation   drop a required validation
  semantic_change     a domain-meaning change none of the above expresses
                      (use ONLY when the others genuinely cannot; you must
                       justify why in `rationale`)

Then supply the patch as an anchored replacement:
  original_snippet  text copied EXACTLY from the file, appearing EXACTLY ONCE
  mutated_snippet   what replaces it

Constraints — a violation of any of these means your mutation is rejected:
- NEVER target a test file. Mutations modify implementation only.
- The change must be <= 15 lines of diff.
- The mutated code must still parse as valid Python.
- original_snippet must be a verbatim substring of the file, whitespace
  included. Copy it; do not retype it.
- The mutation must actually violate the requirement. Cosmetic changes,
  renames, and no-ops are failures.

Also supply violation_scenario: the concrete inputs and state under which
the mutated code produces the prohibited outcome. Be specific — this is
what the repair test will be built from.
```

The `semantic_change` escape hatch exists for exactly one reason: Doc 1 §29's killer case. "Report an 80-of-100 order as completed" is a domain-meaning change that a fixed operator catalogue cannot name. Requiring justification keeps it from becoming the default.

### 5.5 `prompts/repair.md` — Stage 5

```markdown
A requirement violation was introduced into the code and the existing test
suite did not detect it. Write the missing test.

You are given: the requirement, the mutation diff, the violation scenario,
the test output, and the existing tests.

Your test must satisfy BOTH conditions:
  A. It PASSES against the current, correct implementation.
  B. It FAILS when the mutation is applied.

A test that fails everywhere (`assert False`) satisfies B and is worthless.
A test that passes everywhere satisfies A and detects nothing. Both are
rejected automatically.

Requirements:
- Follow the existing suite's conventions: fixtures, imports, naming, style.
- One focused test. Not a suite.
- Assert on the requirement's expected_result — the rejection — not on
  incidental implementation details.
- Use only what the project already depends on.

Emit the complete file contents, ready to write to disk.
```

### 5.6 `prompts/baseline.md` — the control arm

```markdown
You are reviewing whether a test suite adequately protects a requirement.

You have read-only access to the repository and its tests.

Determine whether the existing test suite would detect an implementation
that violates this requirement.

Answer PROTECTED or INSUFFICIENTLY_PROTECTED, with your reasoning.
```

Short on purpose. This is what a competent developer actually types into a coding agent — the honest comparison Doc 2 §24 asks for. Padding it with ReqGuard's methodology would be building a straw baseline in reverse.

---

## 6. Mutation Engine

### 6.1 The reconciliation

Blueprint §11 specifies a **constrained catalogue** ("we do not need a universal mutation engine"). The earlier draft argued for free-form anchored snippets to preserve semantic reach.

**Both, layered:**

```
   mutation_type  ──── from the catalogue (blueprint §11)
                       gives: metric categorisation, plausibility check,
                              a bounded search space, readable reports

   anchored patch ──── original_snippet → mutated_snippet
                       gives: exact application, a reproducible diff,
                              reach beyond what the catalogue can name
```

The type constrains *what kind* of violation is chosen; the anchor makes *applying* it mechanical. Neither half is sufficient alone: a pure catalogue can't express the killer case, and a pure free-form patch can't be categorised in the metrics table.

### 6.2 Per-type plausibility check

Each catalogue type carries a cheap, deterministic assertion — caught before a run is spent:

| Type | Deterministic check on the pair |
|---|---|
| `remove_guard` | mutated is shorter; original contains `if`; a `raise`/`return` disappears |
| `comparison_change` | exactly one comparison operator token differs |
| `boolean_inversion` | a `not` is added or removed, or a boolean operator flips |
| `boundary_shift` | operator moves within `{<, <=, >, >=}`, or a literal shifts by 1 |
| `allow_transition` | a guard on a state field is removed or weakened |
| `remove_validation` | a validation call or `raise` disappears |
| `semantic_change` | no structural check — **`rationale` must be non-empty** |

A mismatch is not fatal; it is logged as `type_mismatch` and the mutation still runs. The check exists to catch a mislabelled mutation polluting the per-category metrics, not to overrule the model.

### 6.3 Application

```python
# src/reqguard/tools/mutation.py
def apply(spec: MutationSpec, sandbox: Path) -> str:
    target = safe_resolve(sandbox, spec.target_file)        # §4.2
    source = target.read_text(encoding="utf-8")

    n = source.count(spec.original_snippet)
    if n == 0:
        raise MutationRejected("anchor_not_found")
    if n > 1:
        raise MutationRejected(f"anchor_ambiguous:{n}")

    mutated = source.replace(spec.original_snippet, spec.mutated_snippet)
    target.write_text(mutated, encoding="utf-8")
    return unified_diff(source, mutated, spec.target_file)
```

Exactly-once matching is the whole safety story. An ambiguous anchor is **rejected, never guessed** — one retry with the failure fed back, then `INCONCLUSIVE`.

### 6.4 The six gates — all must pass before the mutant runs

```python
GATES = [
    ("anchor_unique",   lambda s, src: src.count(s.original_snippet) == 1),
    ("parses",          lambda s, src: _parses(_applied(s, src))),
    ("not_a_test_file", lambda s, src: not is_test_path(s.target_file)),
    ("in_sandbox",      lambda s, src: _contained(s.target_file)),
    ("small_diff",      lambda s, src: _diff_lines(s, src) <= cfg.max_diff_lines),
    ("not_a_noop",      lambda s, src: s.original_snippet != s.mutated_snippet),
]
```

```python
def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (p.name.startswith("test_") or p.name.endswith("_test.py")
            or p.name == "conftest.py" or "tests" in p.parts)
```

### 6.5 The rule that protects the entire result

> **A mutation may never modify a test file.**

If the engine can touch tests, "the tests didn't catch it" becomes trivially achievable and the primary metric means nothing. Enforced in three places:

1. Gate 3 above, before application.
2. `tests/unit/test_mutation_rejects_test_files.py` — a standing unit test.
3. The evaluator re-checks the recorded diff at scoring time (§17).

Three checks for one rule is not paranoia. It is the single assumption every number in the submission rests on.

### 6.6 Equivalent mutants — stated honestly

A mutation can survive because it is *semantically identical* to the original. Classic mutation testing has no general solution, and pretending otherwise is the easiest way to inflate our numbers.

The architecture already answers it: **the repair test is the non-equivalence witness.**

```
mutation survived
      ↓
repair test passes on original AND fails on mutant?
      ├── YES → genuine gap, and it is now closed
      └── NO  → INCONCLUSIVE (possible equivalent mutant)
                reported, and counted against our own precision
```

---

## 7. Sandbox

### 7.1 Strategy

Default **copy**; `worktree` opt-in.

`git worktree` is faster but checks out tracked files only — no `.venv`, no untracked fixtures, no generated data. On an arbitrary user repo that silently breaks the suite, and a broken suite reads as *mutation killed*: **a false negative that looks like success.** Copy is the safe default.

```python
EXCLUDE = {".git", "__pycache__", ".venv", "venv", "node_modules",
           ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
```

### 7.2 Lifecycle — one sandbox per requirement

```
tempfile.mkdtemp(prefix="reqguard-")
      ├── copy repo
      ├── run baseline suite          ← MUST be green (§8.3)
      ├── apply mutation
      ├── run suite                   ← survival verdict
      ├── write repair test
      ├── run repair on ORIGINAL      ← condition A
      ├── run repair on MUTANT        ← condition B
      └── shutil.rmtree()             ← in `finally:`, always
```

```python
@contextmanager
def sandbox(repo: Path, cfg: Config):
    path = Path(tempfile.mkdtemp(prefix="reqguard-"))
    try:
        _materialize(repo, path, cfg)
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
```

`finally` is not optional. A crashed run that leaves mutated trees in `/tmp` is how someone eventually runs a mutant by accident.

### 7.3 Permissions (blueprint §34)

| Scope | Read | Execute | Write |
|---|---|---|---|
| User repository | ✓ | ✗ | **✗ never** |
| Sandbox | ✓ | ✓ pytest, python, git | ✓ mutations, tests, temp |

The single write path into the user's repo is `reqguard apply`, which requires explicit confirmation and refuses unverified repairs (§13.3).

---

## 8. Test Runner

### 8.1 Invocation

```python
subprocess.run(
    shlex.split(cfg.test_command) + ["--json-report",
                                     f"--json-report-file={report}"],
    cwd=sandbox, capture_output=True, text=True,
    timeout=cfg.test_timeout_s, start_new_session=True,
)
```

`start_new_session=True` so a timeout can `killpg` the whole process group. A hung pytest that spawned children will otherwise outlive the run and poison the next one.

If `pytest-json-report` is absent from the target environment, fall back to exit-code-only parsing. Degraded, not broken.

### 8.2 Exit-code taxonomy — blueprint §33, made executable

```python
def classify(exit_code: int, timed_out: bool) -> tuple[RunStatus, str | None]:
    if timed_out:                return RunStatus.TIMEOUT, "suite exceeded timeout"
    match exit_code:
        case 0:  return RunStatus.OK, None                  # all passed
        case 1:  return RunStatus.OK, None                  # tests failed
        case 2:  return RunStatus.ENVIRONMENT_ERROR, "interrupted"
        case 3:  return RunStatus.ENVIRONMENT_ERROR, "internal pytest error"
        case 4:  return RunStatus.ENVIRONMENT_ERROR, "pytest usage error"
        case 5:  return RunStatus.ENVIRONMENT_ERROR, "no tests collected"
        case _:  return RunStatus.ENVIRONMENT_ERROR, f"exit {exit_code}"
```

**Only exit 0 and 1 yield a survival verdict.** Everything else is `ENVIRONMENT_ERROR` → `INCONCLUSIVE`.

This is the most likely way the system lies to us. A missing dependency makes pytest exit 4; read naively as "tests failed", that scores as *mutation killed* and the kill rate rises for free. It gets its own branch, its own unit test, and its own line in the report.

### 8.3 The baseline-green precondition

```python
if baseline.exit_code != 0:
    return Verdict(status="INCONCLUSIVE",
        inconclusive_reason="suite not green before mutation; "
                            "downstream signals would be uninterpretable")
```

Doc 1's entire premise is *"the tests pass — and yet."* If they don't pass to begin with, there is nothing to measure.

### 8.4 Survival

```python
survived = (mutated.exit_code == 0)
```

Four tokens of logic, no model involved. Blueprint §40: *the verifier decides.*

---

## 9. Verification Gate

```python
def verify(repair, original_sb, mutant_sb) -> Verification:
    a = run_single_test(original_sb, repair)    # must PASS
    b = run_single_test(mutant_sb,  repair)     # must FAIL

    ok_a = a.status is RunStatus.OK and a.exit_code == 0
    ok_b = b.status is RunStatus.OK and b.exit_code == 1

    return Verification(
        passes_on_original=ok_a,
        kills_mutant=ok_b,
        verified=ok_a and ok_b,
        failure_reason=_diagnose(a, b, ok_a, ok_b),
    )
```

| passes on original | fails on mutant | Verdict |
|---|---|---|
| ✓ | ✓ | **REPAIRED** |
| ✗ | ✓ | rejected — broken test |
| ✓ | ✗ | rejected — detects nothing |
| ✗ | ✗ | rejected |

Blueprint §14 requires the test to pass *and* kill. Both conditions are load-bearing: B alone is satisfied by `assert False`, which "kills" every mutant and repairs nothing; A alone proves the test is valid but not that it detects anything.

Rejected repairs get `max_repair_attempts` retries with the failure reason fed back, then the requirement is reported `INSUFFICIENTLY_PROTECTED` with no repair — an honest partial result.

---

## 10. Orchestrator

### 10.1 The stage split

Not every stage is an agent. This follows directly from blueprint §40.

| Stage | Mechanism | LLM? |
|---|---|---|
| 1 Requirement Analyzer | `messages.parse` | yes |
| 2 Repository Investigator | Tool Runner + read tools | yes |
| 3 Mutation Planner | `messages.parse` | yes |
| 4 Mutation Executor | pure Python | **no** |
| 5 Test Repairer | Tool Runner + read tools | yes |
| 6 Verification | pure Python | **no** |
| 7 Reporting / Metrics | pure Python | **no** |

The model is never handed a tool that returns a verdict, and never handed a tool that writes to the user's repository.

### 10.2 The loop

```python
def analyze(repo: Path, cfg: Config, trace: Trajectory) -> list[Verdict]:
    requirements = requirement_analyzer.run(cfg.requirements_path, trace)
    verdicts = []

    for req in requirements[:cfg.max_mutations]:
        with sandbox(repo, cfg) as sb:
            base = test_runner.run(sb, cfg)
            if base.exit_code != 0:
                verdicts.append(_inconclusive(req, "suite not green")); continue

            inv  = repository_investigator.run(sb, req, trace)
            if not inv.sites:
                verdicts.append(_inconclusive(req, "no enforcement site")); continue

            spec = mutation_planner.run(sb, req, inv, trace)
            try:
                diff = mutation.apply_with_gates(spec, sb)
            except MutationRejected as e:
                verdicts.append(_inconclusive(req, f"mutation rejected: {e}")); continue

            mutated = test_runner.run(sb, cfg)
            if mutated.status is not RunStatus.OK:
                verdicts.append(_inconclusive(req, mutated.reason)); continue

            if mutated.exit_code == 1:                       # KILLED
                verdicts.append(_protected(req, spec, base, mutated, diff)); continue

            # SURVIVED — the finding
            repair = test_repairer.run(sb, req, spec, mutated, trace)
            ver    = verifier.verify(repair, original_of(sb), sb)
            verdicts.append(_gap(req, spec, base, mutated, diff, repair, ver))

    return verdicts
```

Readable top to bottom, every `continue` a named inconclusive reason. That readability is a feature — this function is what a judge will actually read.

### 10.3 The `pause_turn` assertion

```python
if last is not None and last.stop_reason == "pause_turn":
    raise ReqGuardInternalError(
        "unexpected pause_turn — no server tools are declared")
```

`pause_turn` arises only from server-side tools, and we declare none. If it ever appears, something changed underneath us and we want a loud crash, not a silently truncated investigation.

---

## 11. Trajectory Logging — Blueprint §29 Compliant

### 11.1 The rule and what it changes

Blueprint §29: log **observable actions and concise rationales**, never a hidden reasoning transcript.

This kills the earlier plan to set `thinking={"display": "summarized"}` and pipe reasoning into the log. Instead:

- `thinking` stays **adaptive** (quality) with `display` at its **default `omitted`** — cheaper, and nothing to leak.
- Every structured output model carries an explicit **`rationale: str`** field — one sentence, requested by the prompt, produced as data.

The rationale is a deliberate, model-authored one-liner, not a scraped internal monologue. It is also cheaper and more readable than summarized thinking. Blueprint §29 turns out to be the better engineering call, not just the safer one.

### 11.2 Format

Append-only JSONL, `reqguard-report/trajectory.jsonl`:

```json
{"step":12,"ts":"2026-08-29T10:14:02Z","requirement_id":"REQ-007",
 "stage":"investigate","type":"tool_call","tool":"search_code",
 "input":{"pattern":"def ship"},"result_summary":"3 hits in orders/service.py"}
{"step":13,"stage":"investigate","type":"decision",
 "rationale":"Selected ship_order because it contains the cancellation guard."}
{"step":14,"stage":"plan","type":"api_call","model":"claude-opus-5",
 "request_id":"req_018Ee...","usage":{"input_tokens":12481,"output_tokens":903,
 "cache_read_input_tokens":11200}}
{"step":15,"stage":"execute","type":"verdict","payload":{"survived":true}}
```

Types: `tool_call` · `tool_result` · `decision` · `api_call` · `verdict` · `error`.

Every `api_call` records `response._request_id` and the full `usage`. **Cost in the report is summed from these records, never estimated from a price table** — the report states what was actually spent.

### 11.3 Cache regression alarm

```python
# runs on the 2nd requirement of any run
if usage.cache_read_input_tokens == 0:
    trace.error("cache_miss", "cached prefix was invalidated — check for "
                              "interpolation into the system prompt")
```

A silently broken cache is a 5× cost regression that otherwise surfaces on the invoice.

---

## 12. Reporting

Blueprint §30 exactly:

```
reqguard-report/
├── summary.html          Jinja2, self-contained, no external assets
├── findings.json         one Verdict per requirement
├── mutations.json        specs + diffs
├── metrics.json          evaluator output
└── trajectory.jsonl
```

Plus the terminal render (Doc 1 §15) and, per requirement:

```
mutations/REQ-007.diff    the exact applied diff — reproducible
repairs/REQ-007.py        the proposed test, UNAPPLIED
```

**Repairs are written to the report directory, never to the user's repo.** Applying one is a separate, explicit command.

`summary.html` embeds its CSS inline and references nothing external, so it opens from disk and survives being emailed. It is a generated report — not a web application (blueprint §30, Doc 1 §5).

---

## 13. CLI

### 13.1 Commands

```bash
reqguard analyze .
reqguard analyze . --requirements requirements.md
reqguard analyze . --requirement REQ-007          # the demo path
reqguard analyze . --dry-run                      # stages 1-3, plan only

reqguard apply --requirement REQ-007              # §13.3

reqguard benchmark --arm reqguard|baseline|both
reqguard evaluate
reqguard report
reqguard doctor                                   # §20
```

### 13.2 Progress output — a specified interface, not decoration

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

`Inconclusive` prints even at zero. Hiding it when it's zero trains the reader not to look for it when it isn't.

### 13.3 The only write path into the user's repo

```
$ reqguard apply --requirement REQ-007

Will write:  tests/test_orders_cancelled.py   (new file, 14 lines)

  ✓ verified: passes against current code
  ✓ verified: fails when the cancellation guard is removed

Proceed? [y/N]
```

Refuses if `verification.verified` is false. Refuses to overwrite an existing file. Never invoked by `analyze`.

---

## 14. Configuration

Blueprint §17, with the fields the implementation needs:

```yaml
project:
  language: python              # v1: python only — see §14.1

requirements:
  path: requirements.md

testing:
  command: pytest
  timeout_seconds: 300

analysis:
  max_mutations: 20
  max_repair_attempts: 2
  max_diff_lines: 15

source_paths: [src/]            # mutation targets restricted to these
exclude: ["**/migrations/**"]

safety:
  modify_original: false
  sandbox_strategy: copy        # copy | worktree

agent:
  model: claude-opus-5
  effort:
    analyze: medium
    investigate: high
    plan: xhigh
    repair: high

report:
  output_dir: ./reqguard-report
```

Every field has a default; a repo with no `reqguard.yaml` works.

Project-specific context (Doc 1 §18) comes from an optional `REQGUARD.md` at the repo root, appended to the **investigation user turn** — never to the cached system prefix.

### 14.1 Language scope

v1 is Python + pytest only. The mutant validator uses `ast.parse`; the runner parses pytest exit codes. Both are per-language. The config carries `language:` so the extension point is visible, and the CLI fails clearly rather than misbehaving:

```
$ reqguard analyze ./my-go-project
✗ ReqGuard v1 supports Python/pytest projects only.
  Detected: Go (go.mod). Tracked in ROADMAP.md.
```

---

## 15. Benchmark

### 15.1 Case layout

```
benchmarks/case01-cancelled-order-shipping/
├── requirements.md
├── src/orders/service.py
├── tests/test_orders.py
├── pyproject.toml
└── ground_truth.json
```

```json
{
  "case_id": "case01-cancelled-order-shipping",
  "category": "business_rule",
  "difficulty": "medium",
  "clean": false,
  "requirements": [
    {
      "id": "REQ-01",
      "statement": "Cancelled orders cannot be shipped.",
      "severity": "high",
      "protected": false,
      "expected_mutation": "remove_guard",
      "expected": "survive",
      "rationale": "The guard exists in ship_order(), but the suite only covers the happy path. No test constructs a cancelled order and asserts the rejection."
    }
  ]
}
```

### 15.2 Case 01 in full — the template to clone

```python
# src/orders/service.py
from dataclasses import dataclass

class OrderCancelledError(Exception): ...

@dataclass
class Order:
    id: str
    status: str          # "open" | "cancelled" | "shipped"
    quantity: int

def ship_order(order: Order) -> Order:
    if order.status == "cancelled":            # ← the enforcement point
        raise OrderCancelledError(order.id)
    order.status = "shipped"
    return order
```

```python
# tests/test_orders.py
from orders.service import Order, ship_order

def test_open_order_ships():
    order = Order(id="A1", status="open", quantity=5)
    assert ship_order(order).status == "shipped"

def test_ship_returns_the_order():
    order = Order(id="A2", status="open", quantity=1)
    assert ship_order(order).id == "A2"
# NOTE: nothing constructs a cancelled order. That is the gap.
```

```markdown
<!-- requirements.md -->
# Order Requirements

REQ-01: A cancelled order cannot be shipped. Attempting to ship a
cancelled order must be rejected.
```

Expected trajectory: suite green (2 passed) → investigator finds `ship_order` → planner emits `remove_guard` deleting the two guard lines → suite still green → **SURVIVED** → repairer writes the cancelled-order test → passes on original, fails on mutant → **REPAIRED**.

**Get this one case working end to end before writing case 02.** Blueprint §36: *do not move forward until this works.*

### 15.3 Composition

Blueprint §22 (15 cases) plus blueprint §23 (clean cases):

| Category | Defect cases | Clean cases |
|---|---:|---:|
| Authorization | 3 | 1 |
| State transitions | 3 | — |
| Business rules | 3 | 1 |
| Validation | 2 | — |
| API / error behavior | 2 | 1 |
| Semantic / domain | 2 | — |
| **Subtotal** | **15** | **3** |
| | | **18 total** |

Stretch to 20 with 2 more hard semantic cases.

**Three clean cases, not two.** Blueprint §23 says "at least 2" — but with 2, the measurable false-positive rate is quantised to 0%, 50%, or 100%. Three gives 0/33/67/100. Still coarse, but it can distinguish "one slip" from "systematically cries wolf", which is the distinction blueprint §23 exists to measure. This is the minimum that makes the metric mean anything, and it costs an hour.

### 15.4 The killer case (Doc 1 §29)

`benchmarks/case13-partial-fulfillment/`:

```python
def fulfillment_status(ordered: int, shipped: int) -> str:
    if shipped >= 0:                    # the latent defect
        return "completed"
    return "pending"
```

Tests cover `0 shipped` and `100 shipped`. Nothing covers `80 of 100`.

Ground truth: `protected: false`, `difficulty: hard`, `expected_mutation: semantic_change`.

Expected result: the baseline says PROTECTED (the guard *looks* deliberate, the tests *do* pass); ReqGuard mutates so 80/100 reports COMPLETED, the suite passes, the mutation survives, and the generated partial-fulfilment test kills it.

If ReqGuard can't beat the baseline here, the central hypothesis (Doc 1 §35) is in trouble — and we report that.

### 15.5 `benchmarks/validate.py` — no API calls, runs in CI

For every case, assert:

1. the suite is **green** on unmutated code (§8.3 would otherwise void the case)
2. `ground_truth.json` parses; every requirement has an explicit `protected`
3. every `protected: false` has a stated `rationale`
4. clean cases contain no deliberate defect
5. case ids are unique and match directory names

A silently broken case produces a plausible-looking wrong number.

### 15.6 The freeze rule

Blueprint §25: **no changing benchmark cases after seeing results.**

Enforced mechanically: `reqguard benchmark` records a SHA-256 of the `benchmarks/` tree into `metrics.json`. Comparing two runs with different hashes prints a warning. Honesty is easier when the tooling notices.

---

## 16. Baseline

### 16.1 Implementation

`src/reqguard/evaluation/baseline.py`. Same model, same read-only tool surface, same Tool Runner, one prompt (§5.6). Structured output: a verdict per requirement, scored by the same evaluator against the same ground truth.

### 16.2 Fairness — a stated integrity rule

The baseline gets the **same model** and the **highest effort setting** (`xhigh`). If ReqGuard wins, it must win on workflow.

```
SAME:       model · effort · repository · requirements · tests
            · read-only tool surface · evaluator · ground truth

DIFFERENT:  the workflow, and only the workflow
            baseline → one agent: "does the suite cover this?"
            reqguard → analyze → investigate → mutate → execute
                       → repair → verify
```

Anything else makes the headline number meaningless, and a judge will find it in thirty seconds.

### 16.3 The metric asymmetry — blueprint §26, resolved

Blueprint §26 flags it honestly: the baseline doesn't generate mutations, so *mutation kill rate* isn't directly computable for it.

Resolution — report **two** numbers rather than force one:

**Primary, comparable across both arms: gap-detection accuracy.**
Both arms answer the same yes/no question per requirement — *is this requirement adequately protected?* — scored against ground truth. Precision, recall, and false-positive rate follow.

```
                        Baseline    ReqGuard
gap detection recall      XX%         YY%
precision                 XX%         YY%
clean-case FP rate        XX%         YY%
```

**Secondary, ReqGuard only: requirement mutation kill rate**, plus repair success rate. These measure what the baseline structurally cannot do — and that is itself the finding, stated as a capability difference rather than smuggled into a shared metric.

Both are computed by §17 from the same artifacts. Neither is chosen after seeing the numbers.

---

## 17. Evaluator

`src/reqguard/evaluation/metrics.py`. **Contains no LLM call of any kind.** Deterministic, re-runnable against stored artifacts — re-scoring never costs money, and two people scoring one run get one number.

| Metric | Definition |
|---|---|
| Gap-detection recall | correctly-found gaps / ground-truth gaps |
| Gap-detection precision | correctly-found gaps / all reported gaps |
| Clean-case FP rate | gaps reported on clean cases / clean-case requirements |
| Mutation kill rate | mutations killed / mutations attempted *(ReqGuard only)* |
| Repair success rate | verified repairs / attempted repairs |
| Inconclusive rate | inconclusive / total — **reported, never buried** |
| Cost | summed from trajectory `usage` |
| Runtime | wall clock per case |

It also re-asserts, from the recorded diffs, that no mutation touched a test file (§6.5). A scoring pass that can't be fooled by its own inputs.

Output: `benchmarks/results/<timestamp>/{metrics.json, comparison.md}`.

### 17.1 The numbers rule

No result appears in the README, the report, the slides, or the video unless `reqguard evaluate` produced it from committed artifacts. Placeholders (`XX%`, `YY%`) in Docs 1–3 stay placeholders until a real run replaces them.

---

## 18. Docker

Blueprint §31 — reproducibility is the point, so pin everything.

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache .
COPY . .
ENTRYPOINT ["reqguard"]
```

```yaml
# docker-compose.yml
services:
  benchmark:
    build: .
    environment:
      - ANTHROPIC_API_KEY
    volumes:
      - ./benchmarks/results:/app/benchmarks/results
    command: ["benchmark", "--arm", "both"]
```

```bash
docker compose up --build
docker compose run benchmark
```

The API key passes through the environment — never baked into the image, never committed. `uv.lock` is committed; that is what makes the run reproducible rather than merely repeatable.

---

## 19. Build Order — Hour by Hour, With Gates

Blueprint §36's schedule, each step with a checkable exit condition. **Do not advance past a red gate.**

### Day 1 — Proof of concept, no API key needed

| Hours | Work | Gate |
|---|---|---|
| 1–2 | Skeleton, `pyproject.toml`, `models.py`, `errors.py` | `reqguard --help` exits 0 |
| 2–3 | `tools/repository.py` (7 tools) + `safety.safe_resolve` | unit tests green; `safe_resolve` blocks `../` |
| 3–4 | `benchmarks/case01` in full (§15.2) | `pytest` in the case dir: **2 passed** |
| 4–7 | `tools/mutation.py` — apply + all six gates | **test-file gate unit test passes** |
| 7–9 | `test_runner.py` + exit-code taxonomy | unit test covers exit 0/1/4/5 + timeout |
| 9–11 | `sandbox.py` — copy, containment, teardown | mutation runs in sandbox; original repo byte-identical after |
| 11–12 | Hardcoded end-to-end on case01 (no LLM) | **`requirement → mutation → pytest → SURVIVED`** |

Day 1's entire output is testable **without an API key**. The deterministic half is built first precisely because it is what makes the model's output trustworthy.

### Day 2 — Agent + benchmark

| Time | Work | Gate |
|---|---|---|
| Morning | `agent/client.py`, Stage 1 analyzer | REQ-01 extracted from case01 |
| Morning | Stage 2 investigator (Tool Runner) | `ship_order` located, confidence high |
| Midday | Stage 3 planner + wire to executor | agent-generated mutation survives on case01 |
| Afternoon | Stage 5 repairer | test generated and written to sandbox |
| Afternoon | Stage 6 verifier | **`verified: true` on case01** |
| Evening | cases 02–15 (clone the §15.2 template) | `validate.py` green on all |
| Night | `baseline.py` + `metrics.py` | **first real comparison number** |

By the end of Day 2 the **experiment runs end to end**, even if the numbers are bad. A working experiment on Day 2 beats a better agent on Day 3 with nothing to measure it against.

### Day 3 — Evidence + submission

| Time | Work | Gate |
|---|---|---|
| Morning | Fix the top 2 failure modes from Day 2 | re-run, numbers move |
| Midday | Full benchmark, both arms | `comparison.md` exists |
| Afternoon | MCP adapter **only if** core is stable | one tool callable from a client |
| Afternoon | `summary.html` + trajectory export | opens from disk, no external assets |
| Evening | README + reproduction steps | clean-machine `docker compose` run works |
| Final | 5-minute video · final benchmark | ≤5:00 |

### Cut list, in order

1. MCP adapter (blueprint P2 — it's already labelled optional)
2. SDK surface (P3)
3. `worktree` sandbox strategy
4. Semantic cases beyond the killer case
5. `summary.html` (keep `findings.json` + terminal output)

**Never cut:** the verification gate (§9), the test-file gate (§6.5), the clean cases (§15.3), evaluator-sourced numbers (§17.1). Those four are what make the result mean anything.

---

## 20. Kill Criteria as Executable Checks

Blueprint §37 lists six questions. Make them a command, so the go/no-go decision is a test result rather than a vibe:

```bash
reqguard doctor --case benchmarks/case01-cancelled-order-shipping
```

```
1. understands requirements?      ✓  REQ-01 extracted, schema valid
2. creates meaningful mutations?  ✓  remove_guard, 6/6 gates passed
3. runs tests?                    ✓  2 passed, exit 0, 0.31s
4. distinguishes survive/kill?    ✓  SURVIVED (exit 0 with mutation applied)
5. repairs a surviving case?      ✓  test_cancelled_order_cannot_be_shipped
6. verifies the repair?           ✓  passes on original, fails on mutant

READY
```

Run it at the end of Day 1 (steps 3–4 only) and the end of Day 2 (all six). The blueprint's responses to failure, made concrete:

| Failing check | Response (blueprint §37) |
|---|---|
| 2 unreliable | Drop `semantic_change`; catalogue types only |
| 1 unreliable | Narrow to authorization + business rules |
| 5–6 unreliable | Cut repair; ship detection only, and say so |
| Benchmark shows no improvement over baseline | **Reconsider the project before spending Day 3** |

That last row is the one that takes discipline. It is also the one that separates a research project from a demo.

---

## 21. Conflicts Between the Two Doc-2 Drafts

Settled here; each row states which position this manual implements.

| # | Topic | Blueprint (canonical) | Earlier draft | Implemented |
|---|---|---|---|---|
| 1 | Mutation form | constrained catalogue (§11) | free-form anchored | **Both, layered** (§6.1) — catalogue + `semantic_change` escape hatch |
| 2 | Trajectory content | no hidden CoT (§29) | log summarized thinking | **Blueprint.** `display: omitted` + explicit `rationale` fields (§11.1) |
| 3 | Report format | `summary.html` (§30) | md + json only | **Blueprint.** Jinja2, self-contained (§12) |
| 4 | Benchmark size | 15 + ≥2 clean (§22–23) | 20 + 6 clean | **15 + 3 clean** (§15.3) — 2 clean can't produce a meaningful FP rate |
| 5 | Primary metric | kill rate, asymmetry flagged (§26) | kill rate | **Two metrics** (§16.3) — gap-detection accuracy comparable across arms; kill rate ReqGuard-only |
| 6 | MCP | P2, build if stable (§18–19) | not in v1 | **Blueprint.** Tool layer already MCP-shaped (§4); adapter is Day 3 if green |
| 7 | Tool set | 7 tools incl. `find_symbol`, `find_references`, `git_diff` (§8) | 4 tools | **Blueprint.** With `find_references`'s regex limitation stated in-tool (§4.1) |

Items 4 and 5 are the only places this manual departs from the blueprint, and both are stated with reasons rather than applied quietly. Overrule either in one edit.

---

## 22. Open Questions

Flagged rather than silently decided. None block Day 1.

1. **Requirement discovery with no requirements file.** Extract from docstrings and README, or require `--requirements`? *Leaning: require it in v1; auto-discovery has its own error modes.*
2. **Multiple mutations per requirement.** `max_mutations` is per-run; per-requirement is fixed at 1. Does 3 raise recall enough to justify 3× cost? *Answerable empirically once §17 works — a good v3 experiment.*
3. **Cross-cutting requirements** (logging, rate limiting) have no single enforcement site, and `MutationSpec` assumes one anchor. *Likely `INCONCLUSIVE` in v1; worth one benchmark case to characterise the failure.*
4. **Repair placement.** New file vs. appending to an existing one. *Leaning: new file `tests/test_reqguard_<req_id>.py` — no merge conflicts, trivially reviewable, trivially deletable.*

---

*End of Document 3. The next artifact is code.*
