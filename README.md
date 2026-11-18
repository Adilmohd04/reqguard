# ReqGuard

**Your tests pass — and yet.** ReqGuard checks whether a test suite actually
*protects* what the requirements say, by deriving a minimal, plausible code
change that violates each requirement and seeing whether the suite notices.

```
requirement ─► investigate ─► mutate ─► execute ─► repair ─► verify ─► verdict
   (LLM)          (LLM)        (LLM)     (Python)    (LLM)    (Python)
```

A requirement is **PROTECTED** if the suite catches the mutation,
**INSUFFICIENTLY_PROTECTED** if it doesn't, **REPAIRED** if ReqGuard then wrote a
test that does, and **INCONCLUSIVE** if the environment made the signal
unreadable (a first-class outcome — never silently scored as a pass).

## The bottleneck

Green test suites lie. Coverage tools tell you which *lines* ran, not whether the
*behavior your requirements demand* is actually defended. So the failure mode is
quiet and specific:

> The requirement says "a partially fulfilled order must never be reported as
> completed." The suite tests `0 shipped` and `100 shipped`. It is green. Nobody
> tests `80 of 100` — and the day someone refactors the status logic, an
> `80/100 → completed` bug ships past a passing suite and a passing review.

The person who feels this is whoever owns "are we actually covered?" — a senior
engineer signing off a release, a reviewer on a security- or money-touching PR, a
lead who has learned that "tests pass" and "requirement protected" are not the
same sentence. Their current options are all bad:

- **Read every test and reason about what's missing.** Doesn't scale, and it's
  the exact task humans are worst at — proving the *absence* of a case.
- **Run a mutation-testing tool.** It mutates syntax blindly and reports a score;
  it doesn't know what your requirements *mean*, so it can't tell you which
  surviving mutant corresponds to a requirement anyone cares about.
- **Ask an LLM "is this covered?"** It reasons over the code and gives a plausible
  answer with no execution behind it. Confident, unfalsifiable, sometimes wrong.

**ReqGuard's bet:** the only trustworthy evidence that a suite protects a
requirement is that the suite *fails* when that requirement is violated. So it
derives a minimal, plausible, requirement-specific code change that breaks exactly
one requirement, runs the suite, and reports what actually happened. If the suite
stays green, that's a real gap — with a one-line repro. Then it writes the missing
test and proves it fails on the mutant and passes on the original before showing
it to you.

The distinction that makes this not "just mutation testing" or "just AI test
generation":

```
Traditional coverage:  Code → Tests → which lines ran
Mutation testing:      Code → random mutations → Tests → a score
ReqGuard:              Requirement → intended behavior → requirement-specific
                       violation → Tests → executed verdict → repair → re-verify
```

It starts from the requirement, not the code, and it ends with an executed
outcome, not an opinion.

## Quick start

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"

# A live run uses YOUR OWN model key — ReqGuard ships with none. The client
# is OpenAI-wire, so any OpenAI-compatible endpoint works; set exactly one:
export AEROLINK_API_KEY=...           # aerolink cgapi (gpt-5.6-sol)
#   or: export CONDUIT_API_KEY=...    (+ optional CONDUIT_BASE_URL)
#   or: export OPENROUTER_API_KEY=... (openrouter.ai)
# Offline replay/doctor/CI need NO key at all (see "Reproduce").

reqguard analyze .                    # writes ./reqguard-report/
reqguard analyze . --requirement REQ-07 --dry-run
reqguard apply --requirement REQ-07   # the ONLY write path into your repo
```

**Bring your own key.** ReqGuard does not bundle a model key or credits — you
supply one. The model call surface (`agent/client.py`) is OpenAI-compatible, so
any provider that speaks the OpenAI Chat Completions API works (OpenAI,
OpenRouter, Conduit, aerolink's `cgapi` endpoint, or a local gateway). If more
than one key is set, the client tries them in order (aerolink → Conduit →
OpenRouter) and fails over on a provider error. Everything offline — the replay
corpus, `doctor`, CI — runs with **no key and no spend**.

`reqguard.yaml` is optional — every field has a default. Project context goes
in an optional `REQGUARD.md` at the repo root.

## Add ReqGuard to your own project

ReqGuard runs *against* a repository from the outside — it is a CLI you point at
a project, not a library you import or a dependency you add to
`pyproject.toml`. Nothing of ReqGuard ends up in your shipped code; the only
thing it ever writes into your repo is a single repair test, and only when you
run `apply`.

**1 — install the CLI** (Python 3.12+; isolated, so it never touches your
project's dependencies):

```bash
uv tool install "git+https://github.com/Adilmohd04/reqguard.git"
#   or, without uv:
pipx install "git+https://github.com/Adilmohd04/reqguard.git"
#   or into an existing venv:
pip install "git+https://github.com/Adilmohd04/reqguard.git"
```

**2 — give it a model key.** ReqGuard ships no key or credits. The client is
OpenAI-wire, so any OpenAI-compatible endpoint works. Set one env var (or drop a
`.env` in your repo root — it is auto-loaded, and never overrides a real shell
var):

```bash
export AEROLINK_API_KEY=...           # aerolink cgapi
#   or: export CONDUIT_API_KEY=...    (+ optional CONDUIT_BASE_URL)
#   or: export OPENROUTER_API_KEY=... (openrouter.ai)
#   or point at any other OpenAI-compatible gateway via *_BASE_URL
```

If several are set, the client tries them in order (aerolink → Conduit →
OpenRouter) and fails over on a provider error. Use a genuinely tool-competent
model — it must do both structured output *and* multi-round tool use; most
free-tier models do neither reliably.

**3 — write your requirements.** Create `requirements.md` at your repo root, one
requirement per line, each with a stable id:

```markdown
# Requirements

REQ-01: A refund may never exceed the original charge amount.
REQ-02: A session token must be rejected once it is past its expiry instant.
REQ-03: An order with any unshipped item must never be reported as "completed".
```

**4 — (optional) `reqguard.yaml`** if your layout isn't the default
(`src/` sources, `pytest`, requirements at `requirements.md`):

```yaml
source_paths: ["mypkg/"]
testing:
  command: "pytest -q"
  timeout_seconds: 600
requirements:
  path: "docs/requirements.md"
```

**5 — run it:**

```bash
cd your-project

reqguard analyze .                         # every requirement → ./reqguard-report/
reqguard analyze . --requirement REQ-02    # just one
reqguard analyze . --requirement REQ-02 --dry-run   # stages 1–3, plan only, no suite run

# If a gap was found and a repair verified, write + prove it on your real suite:
reqguard apply --requirement REQ-02 --prove --yes
```

`analyze` never modifies your repo. `apply` is the only write path: it refuses
unverified repairs, never overwrites an existing file, and with `--prove` it
reruns *your* suite and rolls the write back if it isn't green.

**In CI** (offline, no key — gate on the committed report, or run live against a
secret key on protected branches):

```yaml
- run: uv tool install "git+https://github.com/Adilmohd04/reqguard.git"
- run: reqguard analyze . --requirement REQ-02
  env: { OPENROUTER_API_KEY: "${{ secrets.OPENROUTER_API_KEY }}" }
```

## Use ReqGuard from your IDE agent (Kiro, Codex, any MCP client)

ReqGuard ships an MCP server and a skill, so the agent in your IDE can run it
while you build. One command registers both:

```bash
cd your-project
reqguard install kiro      # .kiro/settings/mcp.json + .kiro/skills/reqguard/SKILL.md
reqguard install codex     # .codex/config.toml      + .agents/skills/reqguard/SKILL.md
reqguard install codex --user   # same, but in your home dir, for every project
```

Re-running it is safe. Existing MCP configs are merged, never clobbered, and no
keys are written. Reconnect MCP servers in Kiro, or restart Codex. Then ask the
agent something like *"does my test suite actually protect REQ-02?"* The skill
teaches it the workflow: check setup, analyze, report, and apply a repair only
after you approve.

| MCP tool | What it does | Model calls | Writes |
|---|---|---|---|
| `reqguard_check_setup` | requirements file, green suite (in a sandbox), key names present | no | none |
| `reqguard_init` | scaffold `reqguard.yaml` + `requirements.md` | no | new files only |
| `reqguard_analyze` | the full pipeline; long runs return a `run_id` | yes | `reqguard-report/` |
| `reqguard_status` | poll a running analysis | no | none |
| `reqguard_findings` | verdicts; per requirement: mutation diff + proposed test | no | none |
| `reqguard_apply_repair` | write one *verified* repair test; needs `confirm: true` | no | one new test file |

`apply_repair` is gated three times. The agent must pass `confirm: true`, which
the skill only allows after you say yes. Kiro's config auto-approves only the
read-only tools, so the IDE also prompts you. And `reqguard apply` itself
refuses unverified repairs and overwrites. The server runs over stdio and opens
no network port.

**The interpreter matters.** `install` points the MCP server at the Python it
runs under, and pytest runs with that interpreter unless `testing.command` says
otherwise. Either run `reqguard install` from your project's venv, or set
`testing.command` in `reqguard.yaml` to your venv's pytest. `reqguard_check_setup`
tells you when imports fail for this reason.

## The experiment

ReqGuard is measured against a **baseline** — the same model at the same effort
(`xhigh`) with the same read-only tools, asked the one question a developer
would actually type: *"does the suite cover this?"* Only the workflow differs.

```bash
reqguard benchmark --arm both         # runs both arms over benchmarks/ (spends budget)
reqguard evaluate                     # deterministic scoring — re-runnable for free
```

Primary metric (comparable across both arms): **gap-detection recall / precision
/ clean-case false-positive rate**. Secondary (ReqGuard only, because the
baseline structurally cannot): **mutation kill rate** and **repair success
rate**.

> **Numbers rule (§17.1):** no result appears in any README, report, or slide
> unless `reqguard evaluate` produced it from committed artifacts. The
> placeholders stay placeholders until a real run replaces them.

The version-by-version record of what was tried, what the evidence showed,
and what was kept or dropped as a result — the required **Improvement
Changelog** — is **[`CHANGELOG.md`](CHANGELOG.md)**.

## Validated on real code, not just the benchmark

A perfect score on a self-designed benchmark is a fair thing to be
skeptical of — did the cases get built around what the agent catches? So
alongside the 18-case corpus, ReqGuard was run against
[PyJWT](https://github.com/jpadilla/pyjwt) — a real, widely-used library,
cloned unmodified, never seen by this project before, not part of the
benchmark it's scored against.

ReqGuard mutated the expiry-boundary check in `_validate_exp`
(`exp <= now - leeway` → `exp < now - leeway`). PyJWT's own real test
suite — 365 tests — passed 365/365 against the mutated code. That
boundary has no executable protection in PyJWT's suite as it exists
today. (This is not a claim that it's a bug or a vulnerability — the
inclusive comparison may be entirely deliberate. What's demonstrated is
narrower: a real, executable coverage gap, found and closed, in code
this project never designed around.) ReqGuard then wrote and verified a
test for it.

Every agent trajectory this project produced — this run, the killer case
(offline replay and a real live run), the baseline arm's confidently-wrong
verdict, and the coding agents that built ReqGuard itself — is in
**[`trajectories/`](trajectories/)**, raw JSONL alongside a readable
narrative render for each.

## Reproduce

The whole pipeline — both arms — runs **offline, with zero API spend** against a
committed replay corpus. This is the demo path and the CI path:

```bash
docker compose up --build             # build + benchmark --replay + evaluate, no key
```

or locally:

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
reqguard doctor --case benchmarks/case01-cancelled-order-shipping --replay benchmarks/replay
reqguard benchmark --arm both --replay benchmarks/replay
reqguard evaluate --results benchmarks/results/<timestamp>
```

`reqguard doctor --case <case> --replay benchmarks/replay` runs the six kill
criteria (§20) and is the go/no-go gate; all 18 cases pass it. A live (paid) run
is the same commands **without `--replay`** and needs your own OpenAI-compatible
key — one of `AEROLINK_API_KEY`, `CONDUIT_API_KEY` (+ `CONDUIT_BASE_URL`), or
`OPENROUTER_API_KEY`. ReqGuard ships no key or credits; you bring your own.

## The benchmark corpus

18 cases under `benchmarks/`, each a tiny self-contained project with a
requirement, an implementation, and a green test suite. **15 are defect cases**
(the suite has a real gap, `protected: false`); **3 are clean** (case05, case11,
case17 — the suite genuinely protects the requirement). The clean cases are the
false-positive guard: ReqGuard must *not* flag them. The corpus exercises all
seven mutation types — `remove_guard`, `comparison_change`, `boolean_inversion`,
`boundary_shift`, `allow_transition`, `remove_validation`, and the
`semantic_change` escape hatch.

case13 (partial fulfillment) is the **killer case** (§29): a reasoning-only
baseline calls it protected, but the `80/100 → completed` mutation survives a suite
that only ever tested `0` and `100`. That single contrast is the experiment.

`benchmarks/replay/<case>/` holds the recorded stage outputs that let each case
replay offline.

## Safety

The user's repository is **read-only**. Every mutation runs in a `tempfile`
sandbox copy that is torn down in a `finally:` block. A mutation may never
modify a test file — enforced at the gate, by a standing unit test, and
re-checked from recorded diffs by the evaluator. Repairs are written to the
report directory; `reqguard apply` is the only path that writes into your repo,
it refuses unverified repairs, and it refuses to overwrite.

`reqguard apply --prove` goes one step further: after writing the verified
repair test, it runs your repository's **own** test suite to prove the gap is
actually closed and nothing else broke. If the suite isn't green with the
repair present, the write is rolled back and the repo is left exactly as it
was — so the agent never leaves your code in a broken state. This turns
"trust the report" into "watch the gap close on the real repo," and it runs
offline against the replay corpus with no key:

```bash
reqguard analyze benchmarks/case13-partial-fulfillment --replay benchmarks/replay
reqguard apply benchmarks/case13-partial-fulfillment --requirement REQ-13 \
  --report-dir benchmarks/case13-partial-fulfillment/reqguard-report --yes --prove
# → ✓ repo suite green with the repair: 3 passed, 0 failed (exit 0)
# → ✓ proven: REQ-13 is now protected by an executable test
```

## Layout

| Path | What |
|---|---|
| `src/reqguard/agent/` | the five LLM stages + the single model call surface (`client.py`) |
| `src/reqguard/tools/` | the seven read tools, the mutation engine, the test runner |
| `src/reqguard/verification/` | path containment + the two-condition verifier |
| `src/reqguard/evaluation/` | baseline arm, deterministic evaluator, benchmark scoring |
| `src/reqguard/tracing/` | append-only trajectory log (no hidden reasoning transcript) |
| `benchmarks/` | 18 cases — 15 defect, 3 clean |
| `docs/` | product spec, engineering blueprint (canonical), implementation manual |

## The main failure mode

ReqGuard's mutation gate requires the planner's proposed anchor text to match the
target file **byte-for-byte** — it will not apply a mutation it can't locate
exactly, because guessing is worse than refusing. That precision is where the
system is most fragile: it depends on the model transcribing code exactly,
whitespace included. Run live against [itsdangerous](https://github.com/pallets/itsdangerous),
the planner found the right function and the right kind of mutation, but proposed
an anchor with 20 spaces of indentation where the real file had 12. The gate
correctly refused it — `INCONCLUSIVE`, not a false pass, not a corrupted file —
but that also means a real gap can go unreported simply because the model
couldn't phrase an exact-match anchor for it. The honest failure mode isn't
"finds nothing" or "finds ghosts" — it's "sometimes can't act on what it
correctly found," and it says so rather than hiding it.

## The hot take

The best illustration of why this project exists, ReqGuard found in its own
build, not in a benchmark: this CLI's own exit-code handling had a real bug —
a failing command, including our own go/no-go gate, could silently report
success to the shell. A human caught it by actually checking the real exit
code, not the tool. That's precisely the failure mode ReqGuard exists to
catch in other people's code — a green result that doesn't mean what it
claims to mean — and it's a fair reminder that the discipline has to apply to
the tool itself first, not just the code it's checking.

---

The full story — every provider swap, every real bug found and fixed, as it
happened rather than reconstructed after the fact — is in
**[`COLLAB.md`](COLLAB.md)**. The version history is
**[`CHANGELOG.md`](CHANGELOG.md)**. Raw evidence is in
**[`trajectories/`](trajectories/)**. A slide-form pitch is
**[`ReqGuardpitch.pptx`](ReqGuardpitch.pptx)**.
