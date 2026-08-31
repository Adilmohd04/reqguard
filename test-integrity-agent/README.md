# test-integrity-agent

**Your tests pass. That is not the same as your requirements being protected.**

`tia` takes a repository and a set of written requirements, and answers one
question with evidence instead of opinion: *would this test suite actually catch
a violation of this requirement?*

It answers it by breaking your code on purpose. For each requirement, an LLM
reads the rule and the code that implements it, then writes a minimal, plausible
edit that violates that specific requirement while staying syntactically valid.
Every mutant is applied in an isolated copy of your repo and your suite is run
against it. A mutant the suite **kills** is proof the requirement is defended. A
mutant that **survives** is proof it is not.

```
requirement ──► locate ──► mutate ──► execute ──► score
                (AST+LLM)   (LLM)     (sandbox)
```

## Why a survived mutant matters

Coverage tells you which lines ran. It does not tell you whether anything
*asserted* the behaviour your requirements demand. That gap is where the quiet
bugs live:

> The requirement says *"an order must never be reported as completed unless
> every line item is shipped."* The suite tests `0 shipped` and `all shipped`.
> It is green. Nobody tests `1 of 2` — and the day someone refactors the status
> logic, a partially-shipped order reports as `completed`, past a passing suite
> and a passing review.

A surviving mutant is that bug, found before it is written. It comes with the
exact diff, so it also tells you which test to write.

This is not blind mutation testing. Ordinary mutation tools flip operators at
random and hand you a percentage. They cannot tell you *which requirement* an
escaped mutant threatens, because they have never read your requirements. `tia`
starts from the requirement, so every finding is attached to a rule someone
cared enough about to write down.

---

## Install

```bash
pip install test-integrity-agent
```

Or, to keep it out of your project's environment (recommended — it is a tool,
not a dependency):

```bash
pipx install test-integrity-agent
```

Requires Python 3.10+. Both give you the `tia` command.

From a checkout:

```bash
pip install -e ".[dev]"
```

---

## Set your API key

`tia` reads the key from the `ANTHROPIC_API_KEY` environment variable, and only
from there. It is never read from a config file, never written to a report,
never printed, never passed to the test suite it runs, and it is redacted from
logs. Get one at [console.anthropic.com](https://console.anthropic.com/).

**macOS / Linux (bash, zsh)**

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# make it permanent:
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc   # or ~/.bashrc
```

**Windows — PowerShell**

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# make it permanent for your user:
[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
```

**Windows — cmd.exe**

```cmd
set ANTHROPIC_API_KEY=sk-ant-...
:: permanent:
setx ANTHROPIC_API_KEY "sk-ant-..."
```

**CI (GitHub Actions)**

```yaml
- run: tia scan --json tia-report.json
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

No key? `tia scan --offline` still runs the whole pipeline using AST mutation
operators instead of the LLM. The mutations are mechanical rather than
requirement-directed, and the report says so — but it executes for real and
still finds untested boundaries.

---

## 60-second quickstart

```bash
pipx install test-integrity-agent
export ANTHROPIC_API_KEY="sk-ant-..."

tia example --dest ./tia-demo    # unpack the bundled example repo
cd tia-demo

pytest -q                        # 14 passed — the suite is green
tia scan                         # and yet
```

The example is a small order service with five written requirements and a suite
that passes while leaving several of them undefended.

Here is a **real run**, verbatim — `tia scan --offline -n 2` against the bundled
example on a fresh `pipx` install, with no API key set:

```
test-integrity-agent 0.1.0
repo  .../tia-demo
tests python -m pytest -q
model AST operators (offline)   mutants/req 2   workers 4

                                   Requirement integrity
+-----------------------------------------------------------------------------------------+
| ID      | Requirement                                     | Verdict   | Score | K/S/T/E |
|---------+-------------------------------------------------+-----------+-------+---------|
| REQ-001 | An order must never be reported as completed    | PROTECTED |  100% | 2/0/0/0 |
|         | unless every line item is fully shipped         |           |       |         |
| REQ-002 | A discount must never reduce an order by more   | PARTIAL   |   50% | 1/1/0/0 |
|         | than 50% of its subtotal, whatever the coupon   |           |       |         |
|         | says                                            |           |       |         |
| REQ-003 | A refund must never exceed the amount the       | PROTECTED |  100% | 2/0/0/0 |
|         | customer actually paid                          |           |       |         |
| REQ-004 | An order with no line items must be rejected    | PROTECTED |  100% | 2/0/0/0 |
|         | rather than priced                              |           |       |         |
| REQ-005 | Shipping is free only when the subtotal is at   | PARTIAL   |   50% | 1/1/0/0 |
|         | least 100; below that, a flat fee applies       |           |       |         |
+-----------------------------------------------------------------------------------------+
K/S/T/E = killed / survived / timed out / errored. Score = killed / (killed + survived).

------------------------------------ Survived mutants -------------------------------------
Each block below is a change that broke a requirement while the test suite stayed green.

+- REQ-005 - 1 of 2 mutants survived -----------------------------------------------------+
| Shipping is free only when the subtotal is at least 100; below that, a flat fee applies |
+-----------------------------------------------------------------------------------------+
  src/shop/orders.py - AST operator
  AST operator: loosen the GtE comparison to Gt
--- a/src/shop/orders.py
+++ b/src/shop/orders.py
@@ -75,7 +75,7 @@
     REQ-005: Shipping is free only when the subtotal is at least 100; below
     that, a flat fee applies.
     """
-    if subtotal(order) >= FREE_SHIPPING_THRESHOLD:
+    if subtotal(order) > FREE_SHIPPING_THRESHOLD:
         return 0.0
     return FLAT_SHIPPING_FEE

-------------------------------------------------------------------------------------------
Suite integrity: 80%  (8 killed, 2 survived, 0 timed out, 0 errored across 5 requirements)
10 mutants in 3.0s using (none: AST operators)
Degraded: offline mode: AST operators only, mutations are not requirement-directed
```

Read the REQ-005 survivor: an order at *exactly* 100 stops shipping free, and
not one test notices. The suite checks 150 and 50 — never the boundary the
requirement actually names. That is a one-line fix and a one-line test, and you
would not have found it by reading a coverage report, because those lines are
covered.

With `ANTHROPIC_API_KEY` set, the same run generates requirement-directed
mutations instead of mechanical ones. Those reach further — the `all(...)` →
`any(...)` edit that makes a partially shipped order report as `completed`, for
instance, which no AST operator will find — and each one is explained in terms
of the requirement it breaks rather than the operator that produced it.

`tia` exits **1** when anything survived, so this works as a CI gate out of the box.

---

## Use it in your own project

### 1. As a CLI

From the root of your repository:

```bash
tia init          # writes tia.yaml with every option documented
```

Edit the three lines that matter:

```yaml
requirements:
  - docs/REQUIREMENTS.md    # or a YAML file, or a package with docstrings
source_paths:
  - src/myapp               # only this code is ever mutated
test_command: pytest -q     # how your suite runs, from the repo root
```

Then:

```bash
tia requirements            # check tia sees the rules you expect
tia scan                    # the full run
tia scan --req REQ-003      # iterate on one requirement
tia scan --html report.html # a shareable report
```

**A note on `test_command`.** Point it at the fastest suite that still covers
`source_paths`. `tia` runs it once per mutant, so `pytest tests/unit -q -x -p
no:randomly` is usually the right call, not your full integration suite.

**In CI:**

```yaml
name: test integrity
on: [pull_request]
jobs:
  tia:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[test]" test-integrity-agent
      - run: tia scan --json tia-report.json --html tia-report.html
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: tia-report, path: tia-report.* }
```

Add `--no-fail` while you are working through the first backlog of findings, and
drop it once the suite is clean.

### 2. As a library

```python
from test_integrity_agent import Agent

agent = Agent.from_path(
    "/path/to/repo",
    mutants_per_req=3,
    workers=8,
    model="claude-opus-5",      # optional
)
report = agent.scan()           # or agent.scan(only=["REQ-003"])

print(f"suite integrity: {report.suite_score:.0%}")

for score in report.unprotected:
    print(f"\n{score.requirement.id}: {score.requirement.text}")
    print(f"  {score.survived} of {score.evaluable} mutants survived")
    for survivor in score.survivors:
        print(f"  {survivor.mutation.path}: {survivor.mutation.explanation}")
        print(survivor.diff)
```

Useful pieces of the API:

| Object | What it gives you |
| --- | --- |
| `Agent.from_path(repo, **overrides)` | An agent; overrides are `tia.yaml` field names |
| `agent.scan(only=None)` | Runs everything, returns a `ScanReport` |
| `report.suite_score` | `float` 0–1, or `None` when nothing was evaluable |
| `report.unprotected` | `RequirementScore` list, worst first |
| `report.to_dict()` | The same structure the `--json` output uses |
| `score.survivors` | `MutantResult` list, each with `.diff` and `.mutation` |
| `render_html(report)` / `write_html(report, path)` | The standalone HTML report |
| `to_json(report)` / `write_json(report, path)` | The JSON report |

Errors all derive from `TIAError` and carry a `.message` and a `.hint`:
`MissingAPIKeyError`, `ConfigError`, `NoRequirementsError`,
`RequirementParseError`, `RepositoryError`, `BaselineFailureError`,
`SuiteCommandError`, `LLMError`.

Progress is reportable — pass `progress=fn` where `fn(phase, detail, done, total)`:

```python
agent = Agent.from_path(".", progress=lambda p, d, done, total: print(p, done, total))
```

---

## Writing requirements tia can read

Three formats. Pick whichever your team already uses.

**Markdown** — bullets and rule-shaped headings. An explicit id is used verbatim
and survives reordering; without one, requirements are numbered `REQ-001`,
`REQ-002` in document order.

```markdown
# Requirements
- REQ-001: An order must never be reported as completed unless every line item is shipped.
- SEC-42: A password must never be written to a log.
```

**YAML** — a list of strings, or of mappings:

```yaml
requirements:
  - id: PAY-7
    text: A refund must never exceed the amount the customer actually paid.
    tags: [payments, critical]
```

**Python docstrings** — for requirements that live next to the code:

```python
def refund(order, amount):
    """Refund against an order.

    REQ-003: A refund must never exceed the amount the customer actually paid.
    """
```

Run `tia requirements` to see exactly what was parsed, with ids and sources,
before you spend a cent on a scan.

---

## CLI reference

```
tia init                     write a starter tia.yaml
tia scan                     full run
tia scan --req REQ-003       one requirement (repeatable)
tia report --html out.html   re-render the last scan, no re-run
tia requirements             list the requirements tia parses
tia example --dest ./demo    unpack the bundled example repository
tia clear-cache              delete the on-disk LLM response cache
tia --version
```

### `tia scan`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--repo`, `-C` | cwd | Repository to scan |
| `--config` | `tia.yaml` in the repo | Path to the config file |
| `--req` | all | Only these requirement ids; repeatable |
| `--workers`, `-w` | `4` | Parallel workers for generation *and* test runs |
| `--mutants-per-req`, `-n` | `3` | Candidate mutations per requirement |
| `--timeout` | `120` | Seconds allowed per mutant test run |
| `--test-command` | `pytest -x -q` | How to run the suite |
| `--model` | `claude-sonnet-4-6` | Any current Claude model id |
| `--offline` | off | Skip the LLM; AST operators only |
| `--json PATH` | — | Write the machine-readable report |
| `--html PATH` | — | Write a standalone HTML report |
| `--show-all` | off | Print every survivor, not just the first two |
| `--no-fail` | off | Always exit 0, even with survivors |
| `--verbose`, `-v` | off | Structured logs to stderr |

### `tia report`

`--repo`, `--from PATH` (a report JSON), `--html PATH`, `--json PATH`,
`--show-all`. With no `--from`, it reads the last scan from the cache directory.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Scan completed, nothing survived (or `--no-fail`) |
| `1` | At least one mutant survived |
| `2` | Config, requirements, or repository problem |
| `3` | `ANTHROPIC_API_KEY` not set |
| `4` | The Anthropic API failed |
| `5` | The baseline suite is not green |
| `130` | Interrupted |

---

## tia.yaml

Every key is optional; these are the defaults.

```yaml
# Where the requirements live: markdown, a YAML file, or Python modules whose
# docstrings carry requirement lines. Globs are allowed. Paths are relative to
# the repository root.
requirements:
  - REQUIREMENTS.md

# Source roots that implement those requirements. Mutations are only ever
# applied inside these paths.
source_paths:
  - src

# How to run the test suite, from the root of a sandboxed copy of the repo.
test_command: pytest -x -q

# Per-mutant wall-clock limit in seconds. 1–3600.
timeout: 120

# Candidate mutations generated per requirement. 1–25.
mutants_per_req: 3

# Parallel workers for test execution and mutation generation. 1–64.
# One sandbox copy of your repo is made per worker.
workers: 4

# Claude model used to locate requirements in code and write mutations.
# claude-sonnet-4-6 (default), claude-opus-5 (harder codebases),
# claude-haiku-4-5 (cheapest).
model: claude-sonnet-4-6

# On-disk LLM response cache, relative to the repo root. Reruns are near-free.
cache_dir: .tia_cache

# Exit 1 when any requirement has a surviving mutant.
fail_on_survivor: true

# Extra directory names never copied into a sandbox or scanned for source.
# .git, .venv, node_modules, __pycache__ and friends are always excluded.
exclude_dirs: []
```

There is deliberately **no** key for the API key. Add `.tia_cache/` to your
`.gitignore` (`tia` writes one inside the directory for you).

---

## How the score works

```
integrity = killed / (killed + survived)
```

Timeouts and errors are excluded from the denominator rather than counted as
kills. A timeout means the suite noticed *something* — usually an induced
infinite loop — but not that it asserted anything about the requirement. An
error means the mutant produced no signal at all. Counting either as a kill
would inflate the number in exactly the direction that makes the tool useless.

A requirement with no evaluable mutants scores `INCONCLUSIVE`, never 100%.

| Verdict | Meaning |
| --- | --- |
| `PROTECTED` | Every evaluable mutant was killed |
| `PARTIAL` | Some killed, some survived |
| `UNPROTECTED` | Every evaluable mutant survived |
| `INCONCLUSIVE` | No mutant produced a usable signal — investigate, do not celebrate |

---

## What it costs

Per requirement, `tia` makes one small locate call and one mutation call. With
the default `claude-sonnet-4-6` and `mutants_per_req: 3`:

| Repository | Requirements | Model calls | Typical cost |
| --- | --- | --- | --- |
| The bundled example | 5 | 10 | **~$0.04** |
| A small service | 20 | 40 | **~$0.15** |
| A large codebase | 100 | 200 | **~$0.80** |

Three things keep it there:

- **The disk cache.** Responses are keyed by a hash of the model, prompt and
  schema, so re-running against unchanged code and unchanged requirements costs
  **$0.00**. Only what actually changed is re-generated.
- **Prompt caching** on the system prompt, applied automatically.
- **Model choice.** `--model claude-haiku-4-5` is roughly a third of the cost;
  `--model claude-opus-5` is worth it on subtle domain logic.

The real cost is usually wall-clock, not dollars: your suite runs once per
mutant. `requirements × mutants_per_req` runs, divided by `--workers`. Start
with `-n 1` on a big repo to see the shape before committing to a full sweep.

The exact spend for each run is printed at the end and stored in the JSON report
under `usage` and `estimated_cost_usd`.

---

## Safety

- **Your working tree is never modified.** Every mutant is written into a
  temporary copy. The copies are deleted when the run ends, including after an
  interrupt or a crash. This is covered by a test.
- **Mutation paths cannot escape the sandbox**; a path that resolves outside is
  refused.
- **Your API key is not passed to your test suite.** `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` are stripped from the environment of every test process
  `tia` spawns, and anything key-shaped is redacted from logs.
- **Only `source_paths` is mutated.** Your tests, CI config, and everything else
  are copied but never touched.

---

## Troubleshooting

**`The test suite fails before any mutation is applied` (exit 5).**
`tia` refuses to run against a red suite, because a failing mutant would prove
nothing. Get the suite green, or point `--test-command` at a subset that is.

**Every mutant survived.**
Almost always the tests are importing an *installed* copy of your package rather
than the sandboxed code. `tia` prepends the sandbox to `PYTHONPATH` to win that
race, but a `.pth`-based editable install can still win. Check with
`tia scan -v` and confirm `source_paths` points at the code your tests import.
`pip install -e .` inside the sandbox is not something `tia` does for you — if
your suite needs an install step, make `test_command` do it:
`test_command: sh -c "pip install -qe . && pytest -q"`.

**`No requirements were found`.**
Run `tia requirements` to see what the parser sees. In markdown, requirements
are bullets or rule-shaped headings — plain paragraphs are ignored on purpose.

**`ANTHROPIC_API_KEY is not set` (exit 3).**
See [Set your API key](#set-your-api-key), or run with `--offline`.

**Scans are slow.**
Your suite runs once per mutant. Raise `--workers` (each one costs a repo copy
in disk), lower `-n`, or narrow `--test-command`.

**Mutants time out.**
The default is 120s per mutant. A suite that normally takes 90s needs a higher
`--timeout`; a mutant that induces an infinite loop is correctly reported as a
timeout and is *not* counted as a kill.

**An offline diff shows unrelated formatting changes.**
The AST fallback rebuilds the span with Python's own unparser, which normalises
quote style and line wrapping. Only the flagged change is behavioural; the rest
is cosmetic. The LLM path preserves your formatting, so this is offline-only.

**Rate limited (exit 4).**
Lower `--workers`. Nothing already generated is lost — the disk cache means a
re-run resumes rather than repeats.

**`No such requirement id`.**
Ids are assigned in document order when you do not write them yourself, so
inserting a requirement shifts the ones below it. Write explicit ids
(`REQ-001:`) for anything you want to track over time.

---

## How it works

1. **`requirements.py`** parses markdown, YAML, or docstrings into requirements
   with stable ids.
2. **`locator.py`** indexes every function, method, and class under
   `source_paths`, ranks them against each requirement by token overlap, then
   asks the model which of the top candidates actually implement it. Cached.
3. **`mutator.py`** asks for minimal, plausible, requirement-violating edits of
   those spans. Each candidate must parse and must differ from the original *in
   its AST* — reformatting, comment edits, and docstring rewrites are rejected
   before they cost you a test run. Without an LLM, mechanical AST operators
   (comparison boundaries, dropped guards, flipped booleans, shifted constants)
   take over.
4. **`runner.py`** copies the repo once per worker, applies one mutant at a
   time, runs the suite with a timeout, and restores the file.
5. **`scorer.py`** aggregates. **`report.py`** renders.

## Development

```bash
git clone <repo> && cd test-integrity-agent
pip install -e ".[dev]"
pytest -q          # the package's own suite; no API key needed, no network calls
ruff check . && mypy src
```

The suite runs the real pipeline offline: real sandboxes, real subprocesses,
real pass/fail signal from the bundled example repo.

## License

MIT — see [LICENSE](LICENSE).
