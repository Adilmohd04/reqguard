# ReqGuard — Engineering & Implementation Blueprint

**Document 2: HOW We Build It**
**Version:** 1.0
**Goal:** Turn the approved Product Specification into a working, reproducible, reusable agent.

---

## 1. Engineering Objective

We are **not building a website first**.

We are building a reusable engineering agent with this structure:

```
                         REQGUARD
                            │
                    ┌───────▼────────┐
                    │   AGENT CORE   │
                    └───────┬────────┘
                            │
                       TOOL LAYER
                            │
       ┌────────────────────┼────────────────────┐
       ▼                    ▼                    ▼
 Repository              Mutation             Test
 Inspector               Engine              Runner
       │                    │                    │
       └────────────────────┼────────────────────┘
                            ▼
                       VERIFICATION
                            │
                            ▼
                        REPORTING
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
             CLI            MCP          SDK*
```

`*` SDK is optional for the hackathon if time is limited.

The **core agent must work without a web frontend**.

---

## 2. Recommended Technology Stack

### Primary language: **Python 3.12+**

Reason:

- excellent ecosystem for code analysis
- `pytest`
- AST tooling
- Git integration
- subprocess execution
- mutation testing
- AI SDK support
- easy packaging
- Docker support

---

## 3. Agent Runtime

We should use an established agent SDK rather than inventing an agent framework. The exact SDK version should be pinned during implementation after checking the current official documentation.

```
LLM
 ↓
Agent Runtime
 ↓
ReqGuard Agent
 ↓
Tools
```

The runtime handles: model calls · tool calls · conversation/run state · structured outputs · tracing · errors.

**Our code handles the actual ReqGuard methodology.**

Do not build a custom orchestration framework.

---

## 4. Model Strategy

### Agent reasoning

Use a strong coding/reasoning model for:

- requirement interpretation
- repository investigation
- semantic analysis
- mutation planning
- test generation

### Deterministic execution

Do **not** use an LLM for:

- running tests
- deciding whether a mutation survived
- calculating metrics
- verifying generated tests
- applying benchmark ground truth

That must be ordinary software.

---

## 5. Repository Structure

```
reqguard/
│
├── pyproject.toml
├── README.md
├── LICENSE
├── Dockerfile
├── docker-compose.yml
├── .gitignore
│
├── src/
│   └── reqguard/
│       ├── __init__.py
│       │
│       ├── agent/
│       │   ├── orchestrator.py
│       │   ├── requirement_analyzer.py
│       │   ├── repository_investigator.py
│       │   ├── mutation_planner.py
│       │   └── test_repairer.py
│       │
│       ├── tools/
│       │   ├── filesystem.py
│       │   ├── repository.py
│       │   ├── git.py
│       │   ├── code_search.py
│       │   ├── mutation.py
│       │   ├── test_runner.py
│       │   └── diff.py
│       │
│       ├── verification/
│       │   ├── verifier.py
│       │   ├── mutation_result.py
│       │   └── safety.py
│       │
│       ├── evaluation/
│       │   ├── benchmark.py
│       │   ├── baseline.py
│       │   ├── metrics.py
│       │   └── ground_truth.py
│       │
│       ├── reporting/
│       │   ├── report.py
│       │   └── formatter.py
│       │
│       ├── tracing/
│       │   └── trajectory.py
│       │
│       └── cli/
│           └── main.py
│
├── benchmarks/
│   ├── case01/
│   ├── case02/
│   ├── ...
│   └── case15/
│
├── tests/
│   ├── unit/
│   └── integration/
│
└── examples/
    └── sample-project/
```

---

## 6. Core Agent

The main agent should not be a swarm. We use one orchestrating agent with specialized reasoning stages/tools.

```
ReqGuardAgent
      │
      ├── Requirement Analyzer
      ├── Repository Investigator
      ├── Mutation Planner
      └── Test Repairer
```

These can initially be **logical stages of one agent**, not four independent LLM agents.

This is important. We don't want:

```
Agent 1 talks to Agent 2
Agent 2 talks to Agent 3
Agent 3 talks to Agent 4
```

just for presentation.

---

## 7. Stage 1 — Requirement Analyzer

Input: `requirements.md`

Output should be structured:

```json
{
  "id": "REQ-007",
  "statement": "Cancelled orders cannot be shipped",
  "behavior": {
    "precondition": "order.status == cancelled",
    "prohibited_action": "ship(order)",
    "expected_result": "operation rejected"
  },
  "severity": "high"
}
```

The LLM produces this. Then our program validates the schema.

---

## 8. Stage 2 — Repository Investigator

The agent gets controlled tools:

```
list_files()
read_file()
search_code()
find_symbol()
find_references()
inspect_tests()
git_diff()
```

The agent investigates:

```
Requirement
      ↓
Relevant source files
      ↓
Relevant functions/classes
      ↓
Relevant tests
```

The goal isn't to read the entire repository blindly. The agent should progressively investigate.

---

## 9. Stage 3 — Mutation Planner

Input: requirement model + relevant code + existing tests

Output:

```json
{
  "mutation_type": "remove_guard",
  "target_file": "orders/service.py",
  "target_function": "ship_order",
  "description": "Remove cancelled-order validation",
  "expected_violation": "Cancelled order can be shipped"
}
```

The mutation should represent a **requirement violation**.

---

## 10. Mutation Engine

This part should be deterministic.

The agent proposes: file · location · mutation

The mutation engine:

1. creates isolated worktree
2. applies mutation
3. records diff
4. runs tests
5. records result
6. destroys/reverts environment

Never mutate the user's original repository directly.

---

## 11. Mutation Types

Our initial implementation should support a controlled set.

**Conditional removal**

```python
if user.owner_id != current_user.id:
    raise Unauthorized()
```

becomes:

```python
# guard removed
```

**Comparison change** — `amount <= limit` becomes `amount < limit`

**Boolean inversion** — `if is_authorized:` becomes `if not is_authorized:`

**Boundary modification** — `quantity >= minimum` becomes `quantity > minimum`

**State transition removal** — prevented transition becomes allowed

**Validation removal** — required validation disappears

These are deliberately constrained. **We do not need a universal mutation engine.**

---

## 12. Test Runner

For our initial benchmark: `pytest`

The runner records:

```json
{
  "exit_code": 0,
  "passed": 42,
  "failed": 0,
  "duration_seconds": 3.21
}
```

`Mutation survived` means tests passed despite requirement violation.
`Mutation killed` means tests detected the violation.

---

## 13. Stage 4 — Test Repairer

When `mutation = SURVIVED`, the agent receives: requirement · mutation · test output · relevant code · existing tests.

Then determines what test is missing. It proposes:

```python
def test_cancelled_order_cannot_be_shipped():
    ...
```

The generated test is written into the **isolated environment first**.

---

## 14. Verification Loop

This is critical.

```
Generate test
      ↓
Run test normally
      ↓
Does test itself pass?
      ↓
YES
      ↓
Apply original mutation
      ↓
Run tests
      ↓
Does mutation fail?
      │
      ├── YES → REPAIR VERIFIED
      │
      └── NO  → REPAIR FAILED
```

### A generated test isn't accepted merely because it passes. It must **kill the mutation**.

---

## 15. Safety / Approval

Two environments.

**Original** — `/user/project` — read-only by default.
**Sandbox** — `/tmp/reqguard-run-xxxxx` — all mutations happen there.

```
Original repository
        │
        ▼
    Read / inspect
        │
        ▼
   Create sandbox
        │
        ▼
Mutate / test / repair
        │
        ▼
      Verify
        │
        ▼
Generate proposed patch
        │
        ▼
      USER
        │
        ├── Approve
        └── Reject
```

No silent modification.

---

## 16. CLI

```bash
pip install reqguard          # installation
pip install -e .              # development

reqguard analyze .
reqguard analyze . --requirements requirements.md
reqguard benchmark
reqguard report
```

---

## 17. Configuration

```yaml
# reqguard.yaml
project:
  language: python

requirements:
  path: requirements.md

testing:
  command: pytest

analysis:
  max_mutations: 20
  max_repair_attempts: 2

safety:
  modify_original: false

agent:
  model: <configured-model>
```

This makes ReqGuard customizable rather than hardcoded for our benchmark.

---

## 18. MCP Layer

After the CLI/core works, expose capabilities through MCP.

```
Claude / IDE / Other AI Client
             │
             ▼
        ReqGuard MCP
             │
             ▼
        ReqGuard Core
```

Potential tools:

```
reqguard_analyze_requirement
reqguard_inspect_repository
reqguard_generate_mutation
reqguard_run_mutation
reqguard_repair_test
reqguard_verify
```

The MCP layer should be **thin**. It shouldn't contain the business logic.

---

## 19. Why MCP Comes Later

Because this is a disaster:

```
Agent doesn't work
+
MCP doesn't work
+
CLI doesn't work
```

Instead:

```
Core works
   ↓
CLI works
   ↓
Benchmark works
   ↓
Evaluation works
   ↓
MCP adapter
```

The hackathon judges care primarily about the working engineering system. MCP is valuable because it demonstrates reusability, but it shouldn't consume the time needed to make the actual agent reliable.

---

## 20. SDK

If time remains:

```python
from reqguard import ReqGuard

agent = ReqGuard()
result = agent.analyze(
    repository="./my-project",
    requirements="./requirements.md"
)
```

But this is **P2**. CLI + MCP are sufficient to establish that this isn't merely a website.

---

## 21. Benchmark Design

```
benchmarks/
└── case01/
    ├── requirements.md
    ├── src/
    │   └── orders.py
    ├── tests/
    │   └── test_orders.py
    └── ground_truth.json
```

```json
{
  "requirement": "REQ-01",
  "mutation": "remove_cancelled_guard",
  "expected": "survive"
}
```

---

## 22. Benchmark Cases

Target: **15–20 cases**. Initial 15:

| Category | Cases |
|---|---:|
| Authorization | 3 |
| State transitions | 3 |
| Business rules | 3 |
| Validation | 2 |
| API/error behavior | 2 |
| Semantic/domain behavior | 2 |
| **Total** | **15** |

Then add 2–5 difficult cases if time allows.

---

## 23. Clean Cases

At least **2 clean cases** — no real requirement gap. We measure whether ReqGuard invents problems.

This gives us: true positives · false positives · true negatives · false negatives.

---

## 24. Baseline

Baseline must be implemented **before** ReqGuard becomes sophisticated.

```
Requirement + Repository + Tests
       ↓
General-purpose coding/review agent
       ↓
"Identify requirements that aren't adequately tested."
```

Record: identified gaps · correct gaps · missed gaps · false positives.

Then ReqGuard runs on the exact same cases.

---

## 25. Evaluation Pipeline

```
                   BENCHMARK
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
         BASELINE             REQGUARD
             │                   │
             ▼                   ▼
          Results             Results
             │                   │
             └─────────┬─────────┘
                       ▼
                    METRICS
                       │
                       ▼
                  COMPARISON
```

**No changing benchmark cases after seeing results.**

---

## 26. Primary Metric

### Requirement Mutation Kill Rate

```
mutations killed
────────────────────
mutations attempted
```

We compare baseline vs ReqGuard.

However, if the baseline isn't itself capable of generating mutations, we should define the baseline carefully so the comparison remains scientifically fair. This should be finalized during the first benchmark experiment rather than invented afterward.

---

## 27. Secondary Metrics

```
precision
recall
mutation kill rate
false-positive rate
repair success rate
runtime
token/model cost
human review effort
```

---

## 28. Trajectory Logging

Every agent action gets recorded.

```json
{
  "step": 12,
  "type": "tool_call",
  "tool": "search_code",
  "input": "...",
  "result_summary": "Found ship_order()"
}
```

Then:

```
requirement interpreted
        ↓
files searched
        ↓
function identified
        ↓
mutation planned
        ↓
mutation executed
        ↓
tests passed
        ↓
mutation survived
        ↓
repair generated
        ↓
mutation killed
```

This becomes our representative trajectory for the submission.

---

## 29. Logging Must Not Become Hidden Chain-of-Thought

We should log **observable agent actions and concise rationales**, not private model reasoning.

Good:

```
"Selected ship_order because it contains the authorization guard."
```

Bad:

```
full hidden reasoning transcript
```

This keeps our trajectory useful and safe.

---

## 30. Report Generator

The CLI produces:

```
reqguard-report/
├── summary.html
├── findings.json
├── mutations.json
├── metrics.json
└── trajectory.json
```

For the hackathon, `summary.html` is useful even though the product itself isn't a web application. It's simply a generated report.

---

## 31. Docker

The evaluator must be reproducible.

```bash
docker compose up --build
docker compose run benchmark
```

should produce: benchmark results · baseline results · ReqGuard results · comparison.

Pin: Python version · package versions · test dependencies · model configuration where possible.

---

## 32. Repository Isolation

For the benchmark:

```
benchmark/ → temporary clone → mutation → test → destroy
```

For user repositories:

```
user repo → read-only analysis → sandbox copy
```

This is non-negotiable.

---

## 33. Error Handling

The agent must distinguish:

```
TEST FAILED
MUTATION KILLED
ENVIRONMENT FAILURE
AGENT FAILURE
```

Otherwise our benchmark becomes unreliable.

```json
{
  "status": "environment_error",
  "reason": "dependency installation failed"
}
```

That case should **not** be counted as a mutation kill.

---

## 34. Agent Tool Permissions

**Read** — allowed: read files · search files · inspect git · inspect tests

**Execute** — allowed inside sandbox: pytest · python · git diff · static analysis

**Write** — allowed only inside sandbox: mutation · generated test · temporary files

**Original repository** — no write by default.

---

## 35. Implementation Priority

**P0 — Must work**

```
Python project → Requirement → Repository inspection → Mutation
→ pytest → survive/kill → report
```

**P1** — agent-generated mutation + semantic requirement analysis
**P1** — test repair + verification
**P1** — benchmark + baseline + metrics
**P1** — trajectory logging
**P2** — MCP
**P3** — SDK
**P4** — pretty web demo

---

## 36. Three-Day Build Schedule

### Day 1 — Proof of Concept

| Hours | Work |
|---|---|
| 1–2 | Repository skeleton |
| 2–4 | Requirement parser + repository tools |
| 4–7 | Mutation engine |
| 7–9 | Test runner |
| 9–12 | First complete end-to-end case |

Target: `requirement → mutation → test → survived/killed`

**Do not move forward until this works.**

### Day 2 — Agent + Benchmark

| Time | Work |
|---|---|
| Morning | Requirement reasoning |
| Midday | Mutation planning |
| Afternoon | Test repair |
| Evening | Create 10–15 benchmark cases |
| Night | Baseline + first real measurements |

### Day 3 — Evidence + Submission

| Time | Work |
|---|---|
| Morning | Improve agent based on failures |
| Midday | Run full benchmark |
| Afternoon | MCP integration if core is stable |
| Afternoon/evening | Trajectory logging |
| Evening | README |
| Final hours | 5-minute video · Docker reproduction · final benchmark |

---

## 37. Kill Criteria

At the end of the first development block, we evaluate:

```
Can the system reliably:
1. understand requirements?
2. create meaningful mutations?
3. run tests?
4. distinguish survive/kill?
5. repair a surviving case?
6. verify the repair?
```

- If **mutation generation is unreliable**, simplify it.
- If **semantic reasoning is unreliable**, narrow the requirement categories.
- If **test repair is unreliable**, make the benchmark smaller and focus on detection.
- If **the benchmark shows no meaningful improvement over baseline**, we reconsider the project before wasting the remaining time.

---

## 38. What We Should NOT Optimize First

Do not spend the first day on:

```
UI · logos · landing page · animations · authentication
database · cloud deployment · MCP polish · SDK documentation
```

The winning evidence is:

```
BASELINE → FAILURE → REQGUARD → VERIFIED IMPROVEMENT
```

---

## 39. Final Technical Architecture

```
                       ┌──────────────┐
                       │     CLI      │
                       └──────┬───────┘
                              │
                       ┌──────▼───────┐
                       │ ReqGuard     │
                       │ Orchestrator │
                       └──────┬───────┘
                              │
             ┌────────────────┼────────────────┐
             ▼                ▼                ▼
       Requirement       Repository        Mutation
        Analyzer        Investigator        Planner
             │                │                │
             └────────────────┼────────────────┘
                              ▼
                       ┌───────────────┐
                       │ Mutation      │
                       │ Engine        │
                       └───────┬───────┘
                               ▼
                         Sandbox Repo
                               │
                               ▼
                         Test Runner
                               │
                        ┌──────┴──────┐
                        ▼             ▼
                     KILLED        SURVIVED
                        │             │
                        │             ▼
                        │       Test Repairer
                        │             │
                        │             ▼
                        │       Re-verification
                        │             │
                        └──────┬──────┘
                               ▼
                         Verification
                               │
                               ▼
                           Metrics
                               │
                               ▼
                            Report

                 ┌──────────────────────────┐
                 │       MCP Adapter        │
                 │       (P2)               │
                 └────────────┬─────────────┘
                              │
                     External AI Clients
```

---

## 40. The One Rule for Implementation

> **The agent proposes. The tools execute. The verifier decides.**

```
LLM:
"What should we investigate?"
"What violation should we create?"
"What test might expose it?"

TOOLS:
"Here is the repository."
"Here is the mutation."
"Here is the test result."

VERIFIER:
"The mutation was killed."
"The mutation survived."
"The repair is verified."
```

That separation is what makes this more than an LLM wrapper.

---

## 41. Final Build Target

At the end, a judge should be able to do:

```bash
git clone <repo>
cd reqguard
docker compose up --build
```

See the benchmark run. Then:

```bash
reqguard analyze ./example-project
```

And see ReqGuard investigate a real repository. Then, optionally, connect an MCP-compatible client to the same underlying core.

No website is required. **The agent itself is the product.**

---

## Final architecture decision

**Build the core first. CLI first. Benchmark first. MCP after the core proves itself. Web UI last — or don't build one at all.**

And one final discipline: before implementing the exact agent SDK/MCP APIs, we should verify their **current August 2026 official documentation and versions**, because those interfaces are moving quickly. The architecture above is stable; the exact package/API choices belong in the implementation phase, not the product definition.
