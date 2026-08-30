# Reproducing ReqGuard

This guide takes a clean machine to a fully scored ReqGuard run **offline, with
zero API spend**, and then shows the opt-in paid path for real numbers. Every
command here has been run end-to-end; the offline path is deterministic and
re-runnable for free.

There are two ways to reproduce, in order of preference for a reviewer:

1. **Docker** — one command, no local Python, no key. Recommended.
2. **Local (uv)** — if you want to poke at individual cases.

Both run the *same* replay corpus: the pipeline reads committed JSON fixtures
under `benchmarks/replay/` instead of calling a model, so the analyze →
investigate → mutate → execute → repair → verify pipeline and the baseline arm
both run without a network.

---

## The Numbers Rule (read first)

Per Doc 1 §17.1: **no number appears in any README, report, or slide unless
`reqguard evaluate` produced it from committed artifacts.** The scores you get
from the offline path below are real and reproducible, but they describe the
**replay corpus** — the authored fixtures that encode each case's intended
behavior. They are the CI/demo signal, not a substitute for a paid live run. The
live path (last section) is what produces model-generated numbers. Do not quote
offline replay scores as if they were live-model results.

---

## Prerequisites

| Path | Needs |
|---|---|
| Docker | Docker with Compose v2 (`docker compose`, not `docker-compose`) |
| Local  | Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and nothing else |

No API key is required for either offline path. A key is only needed for the
optional live run at the very end (see that section for which one).

---

## Path 1 — Docker (recommended, zero key, zero spend)

From the repo root:

```bash
docker compose up --build
```

That single command builds the image, runs `benchmark --arm both --replay
benchmarks/replay` over all 18 cases, and then runs `evaluate` (the `evaluate`
service waits for `benchmark` to complete successfully). Results land in
`./benchmarks/results/` and any report in `./reqguard-report/`, both mounted from
the host so they survive the container.

The default `docker compose up` **never** makes a paid call — the live service is
behind an opt-in profile (see the last section). To run only the scoring against
an existing results directory:

```bash
docker compose run evaluate
```

---

## Path 2 — Local with uv (zero key, zero spend)

```bash
# 1. create the environment and install ReqGuard + dev tools
uv venv --python 3.12
uv pip install -e ".[dev]"

# 2. go/no-go gate — the six kill criteria on the reference case (§20)
reqguard doctor --case benchmarks/case01-cancelled-order-shipping --replay benchmarks/replay

# 3. run the full offline benchmark over all 18 cases, both arms
reqguard benchmark --arm both --replay benchmarks/replay

# 4. score the artifacts it just wrote (deterministic, free, re-runnable)
reqguard evaluate --results benchmarks/results/<timestamp-printed-in-step-3>
```

> **Windows note.** The venv interpreter lives at `.venv\Scripts\python.exe` (on
> POSIX it is `.venv/bin/`). If `reqguard` is not on your `PATH` after install,
> invoke the module directly and it behaves identically:
>
> ```powershell
> .venv\Scripts\python.exe -m reqguard.cli.main doctor --case benchmarks/case01-cancelled-order-shipping --replay benchmarks/replay
> ```
>
> The test runner passes `sys.executable` explicitly and forces UTF-8, so the
> `✓`/`✗` gate output renders correctly on both platforms.

### Verifying every case, not just case01

`doctor` is the per-case gate. To confirm the whole corpus is green
(exit 0, all six criteria) — PowerShell:

```powershell
Get-ChildItem benchmarks -Directory -Filter 'case*' | ForEach-Object {
  Write-Output "=== $($_.Name) ==="
  .venv\Scripts\python.exe -m reqguard.cli.main doctor --case "benchmarks/$($_.Name)" --replay benchmarks/replay
}
```

bash:

```bash
for d in benchmarks/case*/; do
  c=$(basename "$d")
  echo "=== $c ==="
  reqguard doctor --case "benchmarks/$c" --replay benchmarks/replay
done
```

Expected: all 18 print `READY`. The 15 defect cases show `SURVIVED` then a
verified repair; the 3 clean cases (case05, case11, case17) show `KILLED` with the
repair steps `n/a — mutation was killed`.

---

## What a correct offline run produces

`reqguard evaluate` emits a `metrics.json` object with a `reqguard` arm and a
`baseline` arm. On the committed replay corpus the shape is:

- **`gap_detection`** for each arm: `recall`, `precision`, `clean_case_fp_rate`,
  `inconclusive_rate`, and a `confusion` block over the 15 defect + 3 clean
  requirements.
- **`mutation`** for the ReqGuard arm only (`kill_rate`, `repair_success_rate`,
  and the attempt/verified counts). The baseline arm's `mutation` is `null` —
  it structurally cannot mutate code or write a test, which is the whole point of
  the comparison.
- **`cost`**: all token and call counts are `0` on the replay path — that is how
  you confirm nothing hit the network.

The reviewer-facing contrast is that the two arms are scored on the **same primary
metric** (gap detection), because the baseline can produce it too; the mutation
and repair rates are ReqGuard-only secondaries.

> These figures come from replayed fixtures encoding intended behavior. They
> demonstrate the pipeline and scoring are wired correctly and reproducible. They
> are **not** a stand-in for the model's live performance — that requires the paid
> run below, and only those numbers may be quoted as live results per §17.1.

---

## Determinism check (optional but convincing)

Because the offline path makes no model calls, two runs score identically. The
evaluator also records a `benchmarks_sha256` over the corpus so you can confirm
you scored the same inputs:

```bash
reqguard evaluate --results benchmarks/results/<ts>   # run twice
# compare metrics.json — identical, including benchmarks_sha256
```

`analyze --record <dir>` on a live run captures stage outputs so a later
`--replay <dir>` reproduces them byte-for-byte; that round-trip is what the
committed fixtures were produced by.

---

## The full offline pipeline test suite

The Python test suite (Tracks A/B/C/D + Wave 2 + replay) runs without a key:

```bash
uv pip install -e ".[dev]"
pytest                      # or: .venv\Scripts\python.exe -m pytest   on Windows
```

The single `@live` API test is skipped without a key. On Windows, a small number
of tests that need `os.symlink` skip unless the process has the symlink privilege
(Developer Mode or an elevated shell); they are guarded, not failures.

---

## Optional — a live, paid run (real model numbers)

This is the **only** path that produces model-generated numbers, and it spends
budget. The model provider has changed as the project evolved (see
`CHANGELOG.md` and `COLLAB.md` for the full history) — it currently runs on
`gpt-5` via a Conduit endpoint, falling back to OpenRouter. It needs one of
these two in the host environment:

```bash
export CONDUIT_API_KEY=...
export CONDUIT_BASE_URL=https://conduit.ozdoev.net/v1
# or, if you don't have a Conduit key:
export OPENROUTER_API_KEY=...
```

Docker (opt-in profile, so a bare `docker compose up` can never trigger it):

```bash
export CONDUIT_API_KEY=...   # or OPENROUTER_API_KEY
docker compose --profile live run benchmark-live
docker compose run evaluate
```

Local:

```bash
export CONDUIT_API_KEY=...            # or: OPENROUTER_API_KEY=...
reqguard benchmark --arm both         # note: NO --replay  → live calls
reqguard evaluate --results benchmarks/results/<ts>
```

To capture a live run so it can be replayed for free afterward:

```bash
reqguard analyze <your-repo> --record benchmarks/replay/<new-case>
```

Only numbers produced by `reqguard evaluate` over a committed live-run results
directory may be quoted as ReqGuard's live performance. Until such a run exists,
the README and reports keep their `[pending: reqguard evaluate]` placeholders.
