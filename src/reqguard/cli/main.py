"""ReqGuard command-line interface. Owned by the Supervisor (Wave 2, S2.2).

Reference: docs/03-implementation-manual.md sections 13, 20.

    reqguard analyze .                      # full run
    reqguard analyze . --requirement REQ-07 # the demo path
    reqguard analyze . --dry-run            # stages 1-3, plan only
    reqguard apply --requirement REQ-07     # the ONLY write path into the repo
    reqguard benchmark --arm both
    reqguard evaluate
    reqguard report
    reqguard doctor --case benchmarks/case01-cancelled-order-shipping
    reqguard install kiro|codex [--user]    # MCP server + skill for the IDE agent
    reqguard mcp                            # serve MCP over stdio (IDEs launch this)
"""

from __future__ import annotations

import json
import contextlib
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import typer

from reqguard.config import load_config
from reqguard.errors import ConfigError, PathEscape, ReqGuardError
from reqguard.verification.safety import safe_resolve

# The terminal report format (Doc 1 section 16) is specified down to its
# checkmarks -- ✓ / ✗ are not decoration, they're the interface. A Windows
# console's default stdout encoding is the locale codepage (cp1252 here),
# which has no mapping for U+2713/U+2717 and raises UnicodeEncodeError on the
# very first `typer.echo`/`print` that uses them. Reconfigure at import time,
# before any command runs, rather than reach for ASCII fallbacks that would
# quietly deviate from the specified output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
    pass

# Load a local .env (API keys, etc.) if one exists, before any command runs and
# before reqguard.agent.client is ever imported. Never overrides a variable
# already set in the real environment (override=False) -- an exported shell
# var always wins over a stale .env. Optional dependency: a missing .env or a
# missing python-dotenv install are both silent no-ops, never a crash, since
# --replay mode needs neither a key nor this file.
#
# usecwd=True is required for a real pip/pipx/uv-tool install: without it,
# find_dotenv() walks up from THIS installed file's location (e.g. inside
# site-packages/, or a uv tool venv), never from the project the user is
# actually analyzing -- so a .env dropped in the target repo's root (exactly
# what the README tells every new user to do) is silently never read. Only
# an editable dev install (`pip install -e .`) happened to hide this, because
# the installed file and the repo root were the same tree.
try:
    from dotenv import find_dotenv, load_dotenv

    # load_dotenv() has no usecwd kwarg of its own -- it only forwards to
    # find_dotenv() when dotenv_path is omitted, and find_dotenv()'s own
    # default (usecwd=False) walks up from THIS file's location, not the
    # caller's. Resolve the path ourselves with usecwd=True instead.
    load_dotenv(dotenv_path=find_dotenv(usecwd=True), override=False)
except ImportError:  # pragma: no cover - dotenv not installed
    pass

app = typer.Typer(add_completion=False, help="Requirement-derived mutation testing.")


def _echo(msg: str = "") -> None:
    typer.echo(msg)


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


@contextmanager
def _live_call_errors():
    """Translate a bare credential-resolution `TypeError` into a clean error.

    **History (COLLAB.md):** written when the client was Anthropic-based —
    `anthropic.Anthropic()` with no credential resolvable raised a bare
    `TypeError`, not a `ReqGuardError`, so it fell through every
    `except ReqGuardError` below as a raw framework traceback. Kept after the
    Gemini/OpenRouter/Conduit migrations as defense in depth, though it's
    currently unreachable: the live client (`agent/client.py::_get_client`)
    now raises a typed `ReqGuardInternalError` directly for a missing key —
    already a `ReqGuardError`, already caught cleanly by this file's general
    handler with the right message. Verified directly: with every provider
    key unset, the real output is
    `✗ no provider key set (CONDUIT_API_KEY or OPENROUTER_API_KEY) -- ...`,
    not what this context manager prints below — no bare TypeError to catch.
    Left in place in case a future provider swap reintroduces the same
    untyped-exception failure mode; if it never fires again, it's harmless.
    """
    try:
        yield
    except TypeError as exc:
        if "Could not resolve authentication method" not in str(exc):
            raise
        _err(
            "✗ no provider credentials found. Set CONDUIT_API_KEY (+ "
            "CONDUIT_BASE_URL) or OPENROUTER_API_KEY.\n"
            "  (--replay needs none of these.)"
        )
        raise typer.Exit(1)


def _guard_python_project(repo: Path) -> None:
    """Fail clearly on a non-Python project rather than misbehaving (section 14.1)."""
    if (repo / "go.mod").is_file():
        _err("✗ ReqGuard v1 supports Python/pytest projects only.\n  Detected: Go (go.mod).")
        raise typer.Exit(2)
    if (repo / "package.json").is_file() and not any(repo.glob("**/*.py")):
        _err("✗ ReqGuard v1 supports Python/pytest projects only.\n  Detected: JavaScript (package.json).")
        raise typer.Exit(2)


@app.command()
def analyze(
    repo: Path = typer.Argument(Path("."), help="Path to the repository to analyze."),
    requirements: Optional[Path] = typer.Option(None, help="Requirements file (overrides config)."),
    requirement: Optional[str] = typer.Option(None, help="Analyze one requirement id only (the demo path)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stages 1-3 only — plan, do not execute."),
    replay: Optional[Path] = typer.Option(None, help="Replay recorded stage outputs — no key, no spend."),
    record: Optional[Path] = typer.Option(None, help="Record this run's stage outputs for later replay."),
) -> None:
    """Run ReqGuard end to end and write ./reqguard-report/."""
    repo = repo.resolve()
    _guard_python_project(repo)
    try:
        cfg = load_config(repo)
    except ConfigError as exc:
        _err(f"✗ {exc}")
        raise typer.Exit(2)
    if requirements is not None:
        cfg.requirements.path = str(requirements)

    from reqguard.agent import orchestrator, requirement_analyzer, mutation_planner, repository_investigator
    from reqguard.reporting.report import write_report
    from reqguard.reporting.formatter import render_terminal
    from reqguard.sandbox import sandbox
    from reqguard.tracing.trajectory import Trajectory

    report_dir = Path(cfg.report.output_dir)
    if not report_dir.is_absolute():
        report_dir = (repo / report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    traj = Trajectory(report_dir / "trajectory.jsonl")

    if dry_run:
        try:
            with _live_call_errors():
                _echo("Analyzing requirements...")
                reqs = requirement_analyzer.run(repo / cfg.requirements_path, orchestrator.TrajectoryTrace(traj))
                if requirement:
                    reqs = [r for r in reqs if r.id == requirement]
                _echo(f"  ✓  {len(reqs)} requirement(s)")
                with sandbox(repo, cfg) as sb:
                    for r in reqs:
                        inv = repository_investigator.run(sb, r)
                        if not inv.sites:
                            _echo(f"  {r.id}: no enforcement site — would be INCONCLUSIVE")
                            continue
                        spec = mutation_planner.run(sb, r, inv)
                        _echo(f"  {r.id}: planned {spec.mutation_type.value} on {spec.target_file}::{spec.target_symbol}")
        except ReqGuardError as exc:
            # Found live, not by inspection: --dry-run had no ReqGuardError
            # handler at all, unlike the full-run path right below it -- a
            # provider error (a transient 503, a missing key, anything)
            # during --dry-run crashed with a raw traceback instead of the
            # same clean "✗ ..." message the full run already gives.
            _err(f"✗ {exc}")
            raise typer.Exit(1)
        raise typer.Exit(0)

    try:
        with _live_call_errors():
            verdicts = orchestrator.analyze(
                repo, cfg, traj, only=requirement, report_dir=report_dir,
                replay_dir=replay, record_dir=record,
            )
    except ReqGuardError as exc:
        _err(f"✗ {exc}")
        raise typer.Exit(1)

    write_report(verdicts, report_dir, repo=str(repo), trajectory_path=report_dir / "trajectory.jsonl")
    _echo(render_terminal(verdicts, str(report_dir) + "/"))


@app.command()
def apply(
    repo: Path = typer.Argument(Path("."), help="Repository the report was generated for."),
    requirement: str = typer.Option(..., help="Requirement id whose verified repair to apply."),
    report_dir: Path = typer.Option(Path("./reqguard-report"), help="Report directory."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    prove: bool = typer.Option(
        False,
        "--prove",
        help="After writing the repair, run the repo's own test suite to prove the "
        "gap is closed and nothing else broke. Rolls the write back if it isn't.",
    ),
) -> None:
    """Write ONE verified repair test into the repo. The only write path (section 13.3)."""
    findings_path = report_dir / "findings.json"
    if not findings_path.is_file():
        _err(f"✗ no report at {report_dir} — run `reqguard analyze` first")
        raise typer.Exit(2)
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    match = next((f for f in findings if f["requirement"]["id"] == requirement), None)
    if match is None:
        _err(f"✗ {requirement} not in the report")
        raise typer.Exit(2)

    ver = match.get("verification")
    if not ver or not ver.get("verified"):
        _err(f"✗ {requirement}: repair is not verified — refusing to apply (section 13.3)")
        raise typer.Exit(1)

    repair = match["repair"]
    # repair["test_path"] came out of findings.json, which traces back to a
    # RepairProposal.test_path the model wrote (Doc 3 section 5.5) -- an
    # ordinary string field, not something structurally guaranteed to stay
    # inside the repo. This is the ONE command that writes to the user's
    # actual repository (section 13.3), so it gets the same containment
    # check the sandbox/mutation path already has, via the same helper
    # (verification.safety.safe_resolve) rather than a second copy of it.
    try:
        target = safe_resolve(repo, repair["test_path"])
    except PathEscape as exc:
        _err(f"✗ refusing to write outside {repo.resolve()}: {exc}")
        raise typer.Exit(1)
    if target.exists():
        _err(f"✗ {target} already exists — refusing to overwrite")
        raise typer.Exit(1)

    _echo(f"Will write:  {repair['test_path']}   (new file)")
    _echo("  ✓ verified: passes against current code")
    _echo("  ✓ verified: fails when the mutation is applied")
    if prove:
        _echo("  → will then run the repo's suite to prove the gap is closed")
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(repair["test_source"], encoding="utf-8")
    _echo(f"✓ wrote {target}")

    if not prove:
        return

    # --prove: demonstrate on the REAL repo (not just the sandbox report) that
    # the newly-written test passes against the current, correct code and the
    # suite as a whole is still green. This is what turns "trust the report"
    # into "watch the gap close." If the suite does not pass, the repo must not
    # be left in a broken state -- roll the write back and fail loudly.
    from reqguard.tools.test_runner import run_tests_with_config
    from reqguard.models import RunStatus

    _echo("\nProving on the real repository...")
    try:
        cfg = load_config(repo)
    except ConfigError as exc:
        _rollback(target, f"could not load config to run the suite: {exc}")
        raise typer.Exit(1)

    outcome = run_tests_with_config(repo.resolve(), cfg)

    if outcome.status is not RunStatus.OK or outcome.exit_code != 0:
        detail = outcome.reason or f"exit {outcome.exit_code}, {outcome.failed} failed"
        _rollback(
            target,
            f"repo suite is not green with the repair present ({detail})",
        )
        _err(
            "  the repair was NOT kept — your repository is unchanged. "
            "This means the verified sandbox result did not reproduce here "
            "(different environment, deps, or config)."
        )
        raise typer.Exit(1)

    _echo(f"  ✓ repo suite green with the repair: {outcome.passed} passed, "
          f"{outcome.failed} failed, {outcome.skipped} skipped (exit 0)")
    _echo(f"✓ proven: {requirement} is now protected by an executable test, "
          "and the rest of the suite still passes.")


def _rollback(target: Path, why: str) -> None:
    """Remove a just-written repair file after a failed --prove, leaving the
    repo exactly as it was. Prunes now-empty parent dirs we may have created."""
    with contextlib.suppress(OSError):
        target.unlink()
    # Best-effort cleanup of empty dirs we created (e.g. tests/nested/deep).
    parent = target.parent
    while parent != parent.parent:
        try:
            parent.rmdir()  # only succeeds if empty
        except OSError:
            break
        parent = parent.parent
    _err(f"✗ {why}")


@app.command()
def benchmark(
    arm: str = typer.Option("both", help="reqguard | baseline | both"),
    benchmarks_dir: Path = typer.Option(Path("benchmarks"), help="Benchmark corpus directory."),
    cases: Optional[str] = typer.Option(None, help="Comma-separated case ids to restrict to."),
    replay: Optional[Path] = typer.Option(None, help="Replay recorded stage outputs — no key, no spend."),
) -> None:
    """Run one or both arms over the benchmark corpus (spends API budget unless --replay)."""
    if arm not in ("reqguard", "baseline", "both"):
        _err("✗ --arm must be reqguard | baseline | both")
        raise typer.Exit(2)
    from reqguard.cli._runner import run_benchmark_arms

    only = [c.strip() for c in cases.split(",")] if cases else None
    try:
        with _live_call_errors():
            results_dir = run_benchmark_arms(benchmarks_dir, arm, only_cases=only, replay_dir=replay)
    except ReqGuardError as exc:
        # Found live: benchmark drives every case in the corpus through a
        # live provider call and had no ReqGuardError handler at all -- one
        # transient error (a 503, a rate limit) partway through a multi-case
        # run crashed the whole batch with a raw traceback instead of the
        # same clean "✗ ..." message every other failing command already
        # gives.
        _err(f"✗ {exc}")
        raise typer.Exit(1)
    _echo(f"✓ artifacts written to {results_dir}")
    _echo(f"  now run: reqguard evaluate --results {results_dir}")


@app.command()
def evaluate(
    results: Optional[Path] = typer.Option(None, help="Results dir (default: newest under benchmarks/results)."),
    benchmarks_dir: Path = typer.Option(Path("benchmarks"), help="Benchmark corpus directory."),
) -> None:
    """Score collected benchmark artifacts. No model calls — re-runnable for free."""
    from reqguard.cli._runner import newest_results_dir, score_results

    rdir = results or newest_results_dir(benchmarks_dir)
    if rdir is None or not rdir.is_dir():
        _err("✗ no results directory — run `reqguard benchmark` first")
        raise typer.Exit(2)
    doc = score_results(rdir, benchmarks_dir)
    _echo(json.dumps(doc, indent=2))


@app.command()
def report(
    report_dir: Path = typer.Option(Path("./reqguard-report"), help="Report directory to re-render."),
) -> None:
    """Re-render summary.html from findings.json in an existing report dir."""
    findings_path = report_dir / "findings.json"
    if not findings_path.is_file():
        _err(f"✗ no findings.json in {report_dir}")
        raise typer.Exit(2)
    from reqguard.models import Verdict
    from reqguard.reporting.report import write_report

    verdicts = [
        Verdict.model_validate(f)
        for f in json.loads(findings_path.read_text(encoding="utf-8"))
    ]
    write_report(verdicts, report_dir)
    _echo(f"✓ re-rendered {report_dir / 'summary.html'}")


@app.command()
def doctor(
    case: Path = typer.Option(..., help="Benchmark case directory to self-check against."),
    replay: Optional[Path] = typer.Option(None, help="Run offline from recorded stage outputs — no key, no spend."),
) -> None:
    """The go/no-go gate: six kill criteria, must be green on case01 (section 20)."""
    from reqguard.cli._runner import run_doctor

    try:
        with _live_call_errors():
            ok = run_doctor(case, replay_dir=replay)
    except ReqGuardError as exc:
        _err(f"✗ {exc}")
        raise typer.Exit(1)
    except FileNotFoundError as exc:
        # Found live: a bad --case or --replay path (a typo, the single most
        # likely real mistake for doctor's actual audience -- a new user
        # running the go/no-go gate for the first time) raised a bare
        # FileNotFoundError with no handler at all, anywhere in this
        # function -- a full raw traceback for what should be the friendliest
        # command in the CLI.
        _err(f"✗ {exc}")
        raise typer.Exit(1)
    raise typer.Exit(0 if ok else 1)


@app.command()
def trajectory(
    path: Path = typer.Argument(..., help="A trajectory.jsonl file, or a report dir containing one."),
    format: str = typer.Option("text", help="text | md — md is ready to paste into the submission."),
) -> None:
    """Render a run's trajectory as the representative narrative (Doc 1 section 27):
    requirement -> investigation -> mutation -> execution -> repair -> verification."""
    from reqguard.cli._runner import render_trajectory

    if format not in ("text", "md"):
        _err("✗ --format must be text | md")
        raise typer.Exit(2)
    try:
        _echo(render_trajectory(path, fmt=format))
    except FileNotFoundError as exc:
        _err(f"✗ {exc}")
        raise typer.Exit(2)


@app.command()
def install(
    ide: str = typer.Argument(..., help="kiro | codex"),
    project: Path = typer.Option(Path("."), help="Project to install into."),
    user: bool = typer.Option(False, "--user", help="Install for every project (home directory)."),
) -> None:
    """Register ReqGuard as an MCP server + skill in Kiro or Codex. Idempotent; merges, never clobbers."""
    from reqguard.integrations.installer import SUPPORTED_IDES
    from reqguard.integrations.installer import install as do_install

    if ide not in SUPPORTED_IDES:
        _err(f"✗ IDE must be one of: {', '.join(SUPPORTED_IDES)}")
        raise typer.Exit(2)
    try:
        result = do_install(ide, project=project, user=user)
    except (ValueError, OSError) as exc:
        _err(f"✗ {exc}")
        raise typer.Exit(1)
    for path in result.written:
        _echo(f"  ✓ wrote {path}")
    for path in result.unchanged:
        _echo(f"  · unchanged {path}")
    for note in result.notes:
        _echo(f"  → {note}")
    _echo(f"✓ ReqGuard installed for {ide}")


@app.command()
def mcp() -> None:
    """Serve ReqGuard over MCP (stdio). IDEs launch this; you rarely run it by hand."""
    from reqguard.integrations.mcp_server import main as serve

    raise typer.Exit(serve())


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - console entry
    # With standalone_mode=False, Typer/Click's own _main() converts an
    # explicitly `raise typer.Exit(N)` inside a command into a plain RETURN
    # VALUE from app(...) -- it does not propagate as an exception. The
    # previous `except typer.Exit` clause here was therefore dead code: it
    # never fired, `app(...)`'s return value was silently discarded, and
    # every `raise typer.Exit(N)` in every command -- including `doctor`,
    # this project's own go/no-go gate (Doc 3 section 20) -- reported exit
    # code 0 to any calling shell or CI system regardless of N. Confirmed
    # directly: app(args=["evaluate", "--results", "nonexistent"],
    # standalone_mode=False) returns the int 2, it does not raise.
    #
    # A second, separate gap in the same standalone_mode=False contract:
    # Click's _main() explicitly RE-RAISES a ClickException (bad flag,
    # missing required option, bad type conversion -- e.g. NoSuchOption,
    # UsageError, MissingParameter) rather than handling it itself, exactly
    # because standalone_mode=False means the caller opted to handle
    # presentation and the exit code. Confirmed directly:
    # main(["analyze", "--this-flag-does-not-exist"]) raised NoSuchOption
    # uncaught -- a raw traceback for the single most common way to misuse
    # a CLI (a typo'd flag), rather than "Error: no such option" + exit 2.
    from typer._click.exceptions import ClickException

    try:
        rv = app(args=argv, standalone_mode=False)
    except SystemExit as exc:
        return int(exc.code or 0)
    except typer.Exit as exc:  # defensive: not currently reachable, cheap to keep
        return int(exc.exit_code)
    except ClickException as exc:
        exc.show()
        return int(exc.exit_code)
    return int(rv) if isinstance(rv, int) else 0


if __name__ == "__main__":
    sys.exit(main())
