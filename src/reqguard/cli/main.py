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
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer

from reqguard.config import load_config
from reqguard.errors import ConfigError, ReqGuardError

# The terminal report format (Doc 1 section 16) is specified down to its
# checkmarks -- ✓ / ✗ are not decoration, they're the interface. A Windows
# console's default stdout encoding is the locale codepage (cp1252 here),
# which has no mapping for U+2713/U+2717 and raises UnicodeEncodeError on the
# very first `typer.echo`/`print` that uses them. Reconfigure at import time,
# before any command runs, rather than reach for ASCII fallbacks that would
# quietly deviate from the specified output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
    pass

app = typer.Typer(add_completion=False, help="Requirement-derived mutation testing.")


def _echo(msg: str = "") -> None:
    typer.echo(msg)


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


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
        raise typer.Exit(0)

    try:
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
    target = repo.resolve() / repair["test_path"]
    if target.exists():
        _err(f"✗ {target} already exists — refusing to overwrite")
        raise typer.Exit(1)

    _echo(f"Will write:  {repair['test_path']}   (new file)")
    _echo("  ✓ verified: passes against current code")
    _echo("  ✓ verified: fails when the mutation is applied")
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(repair["test_source"], encoding="utf-8")
    _echo(f"✓ wrote {target}")


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
    results_dir = run_benchmark_arms(benchmarks_dir, arm, only_cases=only, replay_dir=replay)
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

    ok = run_doctor(case, replay_dir=replay)
    raise typer.Exit(0 if ok else 1)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - console entry
    try:
        app(args=argv, standalone_mode=False)
    except SystemExit as exc:
        return int(exc.code or 0)
    except typer.Exit as exc:
        return int(exc.exit_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
