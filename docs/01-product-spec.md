# ReqGuard — Product Specification

**Document 1: WHAT We Are Building**
**Version:** 1.0
**Purpose:** Final product definition before implementation

---

## 1. Executive Summary

ReqGuard is a reusable **Requirement Integrity Agent** for software projects.

Its purpose is simple:

> Determine whether a project's tests actually protect its stated requirements — and expose requirements that can be violated without the existing test suite noticing.

ReqGuard does this by combining:

1. Requirement understanding
2. Code and test investigation
3. Semantic/behavioral reasoning
4. Adversarial mutation generation
5. Real test execution
6. Test-gap diagnosis
7. Test repair
8. Independent verification

The fundamental idea is:

> A passing test suite does not necessarily mean the requirements are protected.

Instead of merely asking "Do the tests pass?", ReqGuard asks:

> "Can I deliberately violate this requirement, and will the test suite catch me?"

That creates an objective, executable measure of requirement integrity.

---

## 2. The Problem

Modern software projects commonly contain three separate artifacts:

```
Requirements
     +
Implementation
     +
Tests
```

Developers often assume:

```
Tests pass
      ↓
Requirements are protected
```

That assumption can be false.

A requirement may say:

> A user cannot access another user's invoice.

The implementation may contain an authorization check. The test suite may verify:

```
valid user → receives invoice
```

Everything passes. But there may be no test for:

```
User A → requests User B's invoice
```

If the authorization check is accidentally removed:

```
implementation becomes wrong
        ↓
existing tests still PASS
```

The requirement has been violated, but the test suite does not detect it.

This is the central problem ReqGuard addresses.

---

## 3. Who Is ReqGuard For?

### Primary users

**Software engineers** — developers maintaining existing repositories where they need confidence that requirements are actually protected by tests.

### Secondary users

- QA engineers
- Engineering leads
- Developers reviewing AI-generated code
- Teams maintaining legacy codebases
- Teams using coding agents
- Open-source maintainers
- Organizations concerned with regression risk

### Particularly important use case

A developer receives:

```
new requirement
      ↓
AI-generated implementation
      ↓
AI-generated tests
```

ReqGuard asks: **Do these tests actually protect the intended behavior?**

---

## 4. The Core Bottleneck

Traditional testing answers:

> "Does the implementation produce the expected output for the tests we wrote?"

ReqGuard asks a harder question:

> "Would the test suite detect an implementation that violates the requirement?"

That difference is the entire product.

---

## 5. What ReqGuard Is NOT

This is important. ReqGuard is not:

**❌ A generic test generator** — It does not simply generate hundreds of tests.

**❌ A code coverage tool** — High coverage does not necessarily mean strong requirement protection.

**❌ A normal mutation-testing tool** — Mutation testing is an important underlying technique, but ReqGuard starts from requirements and intended behavior, rather than blindly applying generic mutations.

**❌ A chatbot** — The user isn't primarily chatting with an AI.

**❌ A web application** — A web UI can exist later as a demonstration surface, but it is not the product.

**❌ An autonomous code modifier** — ReqGuard should not silently modify a user's project. It proposes changes and requires appropriate approval.

---

## 6. The Core Product

The core ReqGuard workflow is:

```
                  REQUIREMENTS
                       │
                       ▼
              Requirement Analysis
                       │
                       ▼
                Repository Analysis
                       │
              ┌────────┴────────┐
              ▼                 ▼
             CODE             TESTS
              │                 │
              └────────┬────────┘
                       ▼
             Behavioral Model
                       │
                       ▼
             Mutation Planning
                       │
                       ▼
          Requirement-Violating Mutation
                       │
                       ▼
                  Run Tests
                  /       \
                 /         \
             KILLED       SURVIVED
               │             │
               │             ▼
               │       Requirement Gap
               │             │
               │             ▼
               │        Test Repair
               │             │
               │             ▼
               └──────→ Verification
                              │
                              ▼
                       Integrity Report
```

---

## 7. The Most Important Concept: Mutation

A **mutation** is a deliberate modification to the implementation designed to violate a requirement.

### Example

**Requirement:** A cancelled order cannot be shipped.

**Correct implementation:**

```python
if order.status == "cancelled":
    raise OrderCancelledError()

ship(order)
```

ReqGuard may create an adversarial mutation:

```python
ship(order)
```

Then run the existing tests.

**Result A**

```
Test failed
```

Good. The test suite detected the violation.

**Result B**

```
All tests passed
```

Bad. The mutation survived. That means:

> The requirement can currently be violated without the test suite detecting it.

That's the evidence ReqGuard produces.

---

## 8. Semantic Reasoning — The Fusion With Idea #2

This is where ReqGuard becomes more than a conventional mutation-testing wrapper.

The agent must understand what the requirement **means**.

Consider:

> "A partially fulfilled order must not be marked as completed."

A purely syntactic mutation system might not understand this. ReqGuard should investigate:

```
requirement
     ↓
domain terminology
     ↓
state model
     ↓
implementation
     ↓
existing tests
     ↓
possible violation
```

Then generate a meaningful mutation. For example:

```
Correct:

80 shipped / 100 ordered
→ PARTIALLY_FULFILLED


Mutation:

80 shipped / 100 ordered
→ COMPLETED
```

If existing tests still pass:

```
⚠ SEMANTIC REQUIREMENT GAP
```

This is the fusion of our two original ideas:

- **Idea #1:** objective adversarial mutation + verification.
- **Idea #2:** semantic understanding of intended behavior.

---

## 9. The Agent's Responsibilities

ReqGuard's intelligence is divided into purposeful stages.

### A. Requirement Analyzer

Understands:

- requirement
- constraints
- expected behavior
- prohibited behavior
- state transitions
- business rules
- security conditions
- edge cases

**Output:** Requirement model

```json
{
  "requirement": "Cancelled orders cannot be shipped",
  "invariant": "cancelled_order -> ship operation prohibited",
  "violation": "ship cancelled order",
  "severity": "high"
}
```

---

## 10. Repository Investigator

The agent investigates the project. It may inspect:

```
README
requirements
source code
tests
configuration
dependencies
API definitions
domain models
```

It determines where the requirement is implemented.

```
Requirement
    ↓
OrderService
    ↓
Order.status
    ↓
ship()
    ↓
tests/test_orders.py
```

This prevents the agent from generating random mutations.

---

## 11. Mutation Planner

The Mutation Planner asks:

> "What is the smallest realistic change that would violate this requirement?"

### Examples

| Category | Mutation |
|---|---|
| Authorization | Remove ownership check. |
| Validation | Allow invalid input. |
| State transition | Allow forbidden transition. |
| Business rule | Remove a required constraint. |
| Error handling | Return success instead of rejecting invalid operation. |
| Boundary condition | Change `>=` to `>`. |

The mutation should be **plausible, not arbitrary**.

---

## 12. Mutation Executor

This is deterministic infrastructure. It:

1. Creates an isolated copy/worktree
2. Applies mutation
3. Executes the test suite
4. Captures output
5. Records the result
6. Destroys/reverts the mutated environment

The agent does not decide whether the mutation survived. **The test runner does.**

---

## 13. Test Repair Agent

If the mutation survived, ReqGuard investigates why. It then proposes a test that should expose the violation.

```python
def test_cancelled_order_cannot_be_shipped():
    order = create_order(status="cancelled")

    with pytest.raises(OrderCancelledError):
        ship(order)
```

Then ReqGuard reruns the mutation. Expected:

```
mutation
   ↓
new test
   ↓
FAIL
   ↓
mutation KILLED
```

Only then do we consider the repair successful.

---

## 14. Independent Verification

This is one of the most important architectural decisions.

The agent cannot say: *"I fixed it."*

Instead:

```
Agent proposes test
        ↓
actual test runner
        ↓
mutation executed
        ↓
mutation killed?
        ↓
YES / NO
```

The machine provides the evidence. This prevents:

```
LLM → LLM → LLM → "looks good."
```

---

## 15. Output

ReqGuard produces an **Integrity Report**, not just raw JSON.

```
╔════════════════════════════════════════════╗
║             REQGUARD REPORT                ║
╚════════════════════════════════════════════╝

Requirement #07
─────────────────────────────────────────────

Requirement:
Cancelled orders cannot be shipped.

Severity:
HIGH

Status:
⚠ INSUFFICIENTLY PROTECTED

Adversarial mutation:
Removed cancelled-order validation.

Existing tests:
✓ 42 passed

Mutation:
⚠ SURVIVED

Interpretation:
The existing test suite does not detect
this requirement violation.

Generated regression test:
test_cancelled_order_cannot_be_shipped()

Verification:
✓ TEST PASSED
✓ MUTATION KILLED

Recommendation:
Add regression test before accepting changes.
```

This is what the developer acts upon.

---

## 16. The User Experience

The primary experience should be **CLI-first**.

```bash
reqguard analyze .
```

Or:

```bash
reqguard check --requirements requirements.md .
```

Output:

```
Analyzing requirements...       ✓
Mapping implementation...       ✓
Inspecting tests...             ✓
Generating mutations...         ✓
Running verification...         ✓

Requirements analyzed: 15
Protected:              11
Potential gaps:          4

High-risk gaps:          2
Medium-risk gaps:        2

Report:
./reqguard-report/
```

---

## 17. It Is a Reusable Agent

ReqGuard should not fundamentally depend on a website.

```
                   REQGUARD CORE
                        │
              ┌─────────┼─────────┐
              │         │         │
             CLI       MCP       SDK
              │         │         │
              ↓         ↓         ↓
          Developer   AI clients  Other apps
```

The core agent remains the same. The interface can change.

- A developer could eventually use `reqguard analyze .`
- An AI client could access ReqGuard through MCP.
- Another application could embed ReqGuard through an SDK.
- A web interface could be added later.

**The web application is not the agent.**

---

## 18. Customization

Different projects have different requirements. ReqGuard should therefore allow project-specific configuration, e.g. `reqguard.yaml`:

```yaml
language: python

test_command: pytest

requirements:
  - requirements/

rules:
  security: high
  business_logic: high
  error_handling: medium

mutation_limits:
  max_mutations: 20
```

The architecture should also allow the agent to receive project-specific instructions/context.

---

## 19. Safety Model

ReqGuard is an engineering agent. That means it potentially:

- reads source code
- executes tests
- creates temporary mutations
- runs commands
- proposes code changes

Therefore safety matters.

**Default principle:** Read automatically; mutate in isolation; modify the user's project only with approval.

```
User repository
      │
      ├── READ
      │
      ▼
ReqGuard
      │
      ▼
Temporary isolated environment
      │
      ├── MUTATE
      ├── TEST
      └── VERIFY
```

The original repository remains untouched unless the user explicitly approves a proposed change.

---

## 20. Benchmark

This will be a major part of the project. We should build approximately **15–20 controlled cases**. The hackathon brief strongly emphasizes reproducible evaluation, a baseline, comparable cases and challenging/adversarial cases.

### Proposed categories

| Category | Cases |
|---|---|
| Authentication / authorization | 3 |
| Business rules | 3 |
| State transitions | 3 |
| Input/data validation | 3 |
| API behavior / error handling | 3 |
| Semantic / domain behavior | 2–5 |

---

## 21. Ground Truth

Every benchmark case must have known ground truth.

```
Case 07

Requirement:
Cancelled orders cannot ship.

Mutation:
Remove cancellation check.

Expected:
Existing tests should detect violation.

Actual:
Existing tests pass.

Ground truth:
MUTATION SHOULD SURVIVE
```

This gives us objective evaluation.

---

## 22. Clean Cases

We should also include clean requirements/repositories where no deliberate defect exists.

Why? Because an agent that screams `🚨 EVERYTHING IS BROKEN` is useless.

We need to measure **false-positive rate**. The agent must be able to say `Requirement adequately protected.` when it actually is.

---

## 23. Primary Metric

The central metric should be **Requirement Mutation Kill Rate**:

```
Requirement violations detected by tests
────────────────────────────────────────
Requirement violations generated
```

Example:

```
Baseline:
11 / 20 = 55%

ReqGuard:
17 / 20 = 85%

Improvement:
+30 percentage points
```

> Those numbers are targets/examples only until we actually run the benchmark.
> **We will never manufacture results.**

---

## 24. Secondary Metrics

- **Precision** — How many reported gaps are genuine?
- **Recall** — How many real gaps were discovered?
- **Mutation score** — How many adversarial violations were killed?
- **Clean-repository false positives** — How often does ReqGuard report a problem where none exists?
- **Repair success rate** — How many generated tests actually kill the intended mutation?
- **Human effort** — How much developer review time is required?
- **Cost** — Model/API usage.
- **Runtime** — How long analysis takes.

---

## 25. Baseline

We need a credible baseline — something a reasonable developer could actually use.

```
Requirement
     ↓
General-purpose coding agent
     ↓
"Review the tests and determine whether
the requirement is adequately covered."
     ↓
Answer
```

Then we run the same benchmark. Our system gets the same requirements, repositories, tests, and evaluation cases. **The only difference is the workflow.**

---

## 26. The Experiment

```
                 SAME CASES
                     │
          ┌──────────┴──────────┐
          ↓                     ↓
      BASELINE               REQGUARD
          │                     │
          ↓                     ↓
      RESULTS                RESULTS
          │                     │
          └──────────┬──────────┘
                     ↓
              COMPARE RESULTS
```

Then we can demonstrate:

```
Baseline mutation kill rate:    XX%
ReqGuard mutation kill rate:    YY%

Improvement:                     +ZZ pp
```

Actual values will come from our benchmark.

---

## 27. Agent Trajectory

The hackathon also wants representative agent trajectories. ReqGuard naturally produces one.

```
Requirement identified
       ↓
Relevant files discovered
       ↓
Authorization invariant extracted
       ↓
Mutation hypothesis generated
       ↓
Mutation applied
       ↓
17 tests executed
       ↓
17 passed
       ↓
MUTATION SURVIVED
       ↓
Evidence reviewed
       ↓
Missing negative test identified
       ↓
Test generated
       ↓
Mutation rerun
       ↓
Mutation killed
       ↓
Requirement protection verified
```

That is a strong trajectory because the judge can see: **reasoning → tool → observation → adaptation → verification.**

---

## 28. Changelog

We should intentionally experiment.

```
v0  General LLM review           → Baseline established
v1  Requirement extraction       → Improvement measured
v2  Adversarial mutations        → Improvement measured
v3  Semantic mutation planning   → Improvement measured
v4  Test repair                  → Improvement measured
v5  Independent verification     → Improvement measured
v6  Remove unnecessary component → No measurable regression
```

If something doesn't help, we remove it. That becomes evidence of engineering judgment rather than architecture theater.

---

## 29. The Killer Case

We need one case specifically designed to defeat naïve approaches.

**Requirement:**

> "A partially fulfilled order must never be reported as completed."

**Existing implementation:**

```python
if shipped_quantity >= 0:
    status = "completed"
```

**Existing tests cover:**

```
0 shipped
100 shipped
```

but not:

```
80 shipped / 100 ordered
```

A normal review might miss the semantic problem. ReqGuard creates `80 / 100 → COMPLETED`. Existing tests `PASS`. Mutation `SURVIVED`.

ReqGuard then:

```
Identifies semantic gap
       ↓
Generates partial-fulfillment test
       ↓
Mutation fails
       ↓
Requirement now protected
```

That is the demo we want judges to remember.

---

## 30. What Makes This Different

We should **NOT** claim "Nobody has ever done mutation testing." That would be false.
We should **NOT** claim "Nobody has ever generated tests with AI." Also false.

Our claim is narrower:

> ReqGuard connects natural-language requirements to adversarial, requirement-specific mutations and uses executable test outcomes to determine whether the requirement is actually protected.

The distinction is:

```
Traditional:
Code → Tests → Coverage

Mutation testing:
Code → Mutations → Tests → Mutation score

ReqGuard:
Requirement
    ↓
Intended behavior
    ↓
Requirement-specific violation
    ↓
Tests
    ↓
Executable evidence
    ↓
Repair
    ↓
Re-verification
```

That is our product thesis.

---

## 31. Why an Agent Is Necessary

We should be able to answer the judge's question: *"Why couldn't a normal script do this?"*

Because a deterministic mutation engine can create syntactic mutations. But it doesn't necessarily know: *"What does this particular requirement mean?"*

ReqGuard needs to reason across:

```
requirement + code + tests + domain terminology
+ state transitions + existing behavior
```

Then it chooses a meaningful violation.

| Deterministic system handles | The agent handles |
|---|---|
| mutation execution | understanding |
| test execution | investigation |
| verification | hypothesis |
| metrics | planning |
| | repair |

That division is fundamental.

---

## 32. What We Are NOT Going to Build

To protect the three-day scope. Not initially:

- SaaS platform
- User accounts
- Payment system
- Cloud dashboard
- Database
- Browser extension
- VS Code extension
- Mobile app
- Large multi-agent swarm
- Custom AI model
- Training pipeline

The first version is:

```
CLI
+
agent core
+
repository tools
+
mutation engine
+
test runner
+
verification
+
benchmark
+
report
```

MCP/SDK integration can follow once the core works.

---

## 33. Definition of Done

ReqGuard is not done because the agent produces an impressive explanation. It is done when we can demonstrate:

### Functional

- [ ] reads a repository
- [ ] understands specified requirements
- [ ] finds relevant implementation
- [ ] generates meaningful violations
- [ ] runs tests
- [ ] detects surviving mutations
- [ ] generates/repairs tests
- [ ] verifies the repair
- [ ] produces an actionable report

### Experimental

- [ ] baseline exists
- [ ] benchmark exists
- [ ] ground truth exists
- [ ] ≥10 cases
- [ ] difficult case exists
- [ ] clean cases exist
- [ ] same cases run against baseline and ReqGuard
- [ ] primary metric calculated
- [ ] secondary metrics calculated

### Engineering

- [ ] isolated mutation environment
- [ ] no unauthorized repository modification
- [ ] reproducible installation
- [ ] deterministic evaluator
- [ ] trajectory logging

### Submission

- [ ] README
- [ ] changelog
- [ ] reproduction instructions
- [ ] benchmark results
- [ ] representative trajectories
- [ ] ≤5-minute demo

---

## 34. The 5-Minute Story

| Time | Segment | Content |
|---|---|---|
| 0:00–0:45 | **Problem** | "Your tests pass. Your requirement is still broken." |
| 0:45–1:30 | **Baseline** | Show a normal agent reviewing the repository → "Looks adequately tested." |
| 1:30–2:30 | **ReqGuard** | Requirement → mutation hypothesis → mutation → tests pass → SURVIVED |
| 2:30–3:30 | **Repair** | Generate stronger test → rerun mutation → KILLED |
| 3:30–4:15 | **Benchmark** | Baseline XX% / ReqGuard YY% / Improvement +ZZ pp |
| 4:15–5:00 | **Insight** | "Passing tests tell us that tested scenarios work. Requirement mutation tells us whether the requirement is actually protected." Then show the trajectory. |

---

## 35. The Central Research Hypothesis

This should guide the whole project:

> A requirement-driven adversarial agent can identify weaknesses in test suites that are missed by ordinary test review, and independently verified mutation outcomes can provide a more objective measure of requirement protection.

We don't assume this is true. **We test it.** That distinction is important.

---

## 36. The Product in One Sentence

**"What did you build?"**

> ReqGuard is a reusable agent that turns software requirements into adversarial violations, tests whether existing tests catch those violations, and repairs and verifies gaps that survive.

**"What's novel?"**

> We don't measure whether the tests pass — we deliberately make the requirement fail and measure whether the tests notice.

---

## 37. Final Product Architecture

```
                         REQGUARD
                            │
                    ┌───────┴────────┐
                    │   AGENT CORE   │
                    │                │
                    │ Requirement    │
                    │ Understanding  │
                    │                │
                    │ Investigation  │
                    │                │
                    │ Mutation       │
                    │ Planning       │
                    │                │
                    │ Test Repair    │
                    └───────┬────────┘
                            │
                       TOOL LAYER
                            │
          ┌─────────────────┼─────────────────┐
          ↓                 ↓                 ↓
      Repository         Mutation          Test
       Tools             Engine           Runner
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ↓
                      VERIFICATION
                            │
                            ↓
                      EVALUATION
                            │
                            ↓
                       REPORTING
                            │
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
             CLI            MCP           SDK
```

---

## Next Document

The next document should **not** redefine the product. It should translate this exact specification into an engineering implementation:

which model · which agent SDK/runtime · whether MCP is included · Python packages · repository structure · mutation mechanism · sandbox · prompts · tool schemas · benchmark generator · trajectory logger · evaluator · CLI · Docker · and the exact build sequence.
