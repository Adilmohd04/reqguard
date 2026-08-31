"""The ``tia`` command line.

    tia init                        write a starter tia.yaml
    tia scan                        full run
    tia scan --req REQ-003          one requirement
    tia report --html out.html      re-render the last scan
    tia example --dest ./demo       unpack the bundled example repository
    tia requirements                list the requirements tia can see

Exit codes are stable and meant for CI:

    0  scan completed, nothing survived (or --no-fail was passed)
    1  at least one mutant survived
    2  configuration, requirements, or repository problem
    3  ANTHROPIC_API_KEY missing
    4  the Anthropic API failed
    5  the baseline test suite is not green
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from . import __version__
from .agent import Agent
from .config import Config, api_key_present, write_starter_config
from .errors import TIAError
from .example import EXAMPLE_NAME, copy_example
from .logging_setup import configure_logging
from .models import ScanReport
from .report import render_terminal, write_html, write_json
from .requirements import load_requirements

app = typer.Typer(
    name="tia",
    help=(
        "Find out whether your test suite would actually catch violations of your "
        "written requirements."
    ),
    no_args_is_help=True,
    add_completion=False,
)

def _tolerate_narrow_encodings() -> None:
    """Never crash because the terminal cannot encode a character.

    A legacy Windows console is cp1252; a box-drawing character raises
    UnicodeEncodeError mid-report and takes the whole run with it after the
    work is already done. Degrading one glyph is always the better trade.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream not reconfigurable
            pass


_tolerate_narrow_encodings()

console = Console(safe_box=True)
err_console = Console(stderr=True, safe_box=True)

LAST_REPORT_NAME = "last-report.json"


def _fail(exc: TIAError) -> None:
    err_console.print(f"\n[bold red]Error:[/bold red] {exc.message}")
    if exc.hint:
        err_console.print(f"\n[bold]Hint:[/bold] {exc.hint}")
    raise typer.Exit(code=exc.exit_code)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"test-integrity-agent {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Requirement-directed mutation testing for Python repositories."""


@app.command()
def init(
    repo: Path = typer.Option(
        Path.cwd(), "--repo", "-C", help="Repository to write tia.yaml into."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing tia.yaml."),
) -> None:
    """Write a starter tia.yaml with every option documented."""
    try:
        target = write_starter_config(repo.expanduser().resolve(), force=force)
    except TIAError as exc:
        _fail(exc)
        return
    console.print(f"[green]Wrote[/green] {target}")
    console.print(
        "\nNext: point [cyan]requirements[/cyan] and [cyan]source_paths[/cyan] at your "
        "files, set [cyan]test_command[/cyan], then run [bold]tia scan[/bold]."
    )
    if not api_key_present():
        console.print(
            "\n[yellow]ANTHROPIC_API_KEY is not set.[/yellow] Set it before scanning, "
            "or use [bold]tia scan --offline[/bold] for AST-only mutations."
        )


@app.command()
def scan(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-C", help="Repository to scan."),
    config_path: Path | None = typer.Option(
        None, "--config", help="Path to tia.yaml (default: tia.yaml in the repo)."
    ),
    req: list[str] | None = typer.Option(
        None, "--req", help="Only scan these requirement ids. Repeatable."
    ),
    workers: int | None = typer.Option(
        None, "--workers", "-w", help="Parallel workers for generation and test runs."
    ),
    mutants_per_req: int | None = typer.Option(
        None, "--mutants-per-req", "-n", help="Candidate mutations per requirement."
    ),
    timeout: int | None = typer.Option(
        None, "--timeout", help="Seconds allowed per mutant test run."
    ),
    test_command: str | None = typer.Option(
        None, "--test-command", help='How to run the suite, e.g. "pytest -x -q".'
    ),
    model: str | None = typer.Option(
        None, "--model", help="Claude model id (default: claude-sonnet-4-6)."
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Skip the LLM and use AST mutation operators only."
    ),
    json_out: Path | None = typer.Option(
        None, "--json", help="Write the machine-readable report here."
    ),
    html_out: Path | None = typer.Option(
        None, "--html", help="Write a standalone HTML report here."
    ),
    show_all: bool = typer.Option(
        False, "--show-all", help="Show every survived mutant, not just the first two."
    ),
    no_fail: bool = typer.Option(
        False, "--no-fail", help="Always exit 0, even when mutants survive."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose structured logs."),
) -> None:
    """Run the full pipeline: locate, mutate, execute, score."""
    configure_logging(verbose=verbose)
    try:
        agent = _build_agent(
            repo=repo,
            config_path=config_path,
            workers=workers,
            mutants_per_req=mutants_per_req,
            timeout=timeout,
            test_command=test_command,
            model=model,
            offline=offline,
            verbose=verbose,
        )
    except TIAError as exc:
        _fail(exc)
        return

    _print_preamble(agent.config)

    columns = (
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
    try:
        with Progress(*columns, console=console, transient=True) as progress:
            tasks: dict[str, TaskID] = {}
            labels = {
                "plan": "Locating and mutating",
                "baseline": "Checking the baseline suite",
                "run": "Running mutants",
            }

            def hook(phase: str, detail: str, done: int, total: int) -> None:
                if phase not in tasks:
                    tasks[phase] = progress.add_task(labels.get(phase, phase), total=total)
                progress.update(
                    tasks[phase],
                    completed=done,
                    total=total,
                    description=f"{labels.get(phase, phase)} [dim]{detail}[/dim]",
                )

            agent.progress = hook
            report = agent.scan(only=req or None)
    except TIAError as exc:
        _fail(exc)
        return
    except KeyboardInterrupt:
        err_console.print("\n[yellow]Interrupted.[/yellow] No files in your repository "
                          "were modified.")
        raise typer.Exit(code=130) from None

    render_terminal(report, console, show_all=show_all)
    _write_outputs(agent, report, json_out, html_out)

    if report.total_survived and agent.config.fail_on_survivor and not no_fail:
        raise typer.Exit(code=1)


@app.command()
def report(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-C", help="Repository the scan ran on."),
    from_json: Path | None = typer.Option(
        None, "--from", help="Read a report JSON instead of the last cached scan."
    ),
    html_out: Path | None = typer.Option(
        None, "--html", help="Write a standalone HTML report here."
    ),
    json_out: Path | None = typer.Option(None, "--json", help="Copy the report JSON here."),
    show_all: bool = typer.Option(False, "--show-all", help="Show every survived mutant."),
) -> None:
    """Re-render the most recent scan without re-running anything."""
    try:
        config = Config.load(repo.expanduser().resolve())
    except TIAError as exc:
        _fail(exc)
        return

    source = from_json or (config.cache_path / LAST_REPORT_NAME)
    if not source.is_file():
        err_console.print(
            f"\n[bold red]Error:[/bold red] No saved report at {source}.\n\n"
            "[bold]Hint:[/bold] Run [bold]tia scan[/bold] first, or pass "
            "[cyan]--from path/to/report.json[/cyan]."
        )
        raise typer.Exit(code=2)

    try:
        loaded = _load_report(source)
    except TIAError as exc:
        _fail(exc)
        return

    render_terminal(loaded, console, show_all=show_all)
    if html_out:
        console.print(f"[green]HTML report:[/green] {write_html(loaded, html_out)}")
    if json_out:
        console.print(f"[green]JSON report:[/green] {write_json(loaded, json_out)}")


@app.command()
def requirements(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-C", help="Repository to read."),
    config_path: Path | None = typer.Option(None, "--config", help="Path to tia.yaml."),
) -> None:
    """List the requirements tia parses out of your sources, with their ids."""
    try:
        config = Config.load(repo.expanduser().resolve(), config_path)
        parsed = load_requirements(
            config.resolved_requirement_paths(), config.repo, config.excluded
        )
    except TIAError as exc:
        _fail(exc)
        return

    from rich.table import Table

    table = Table(header_style="bold", expand=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Requirement", overflow="fold")
    table.add_column("Source", style="dim", no_wrap=True)
    for requirement in parsed:
        table.add_row(requirement.id, requirement.text, requirement.source)
    console.print(table)
    console.print(f"[dim]{len(parsed)} requirements[/dim]")


@app.command()
def example(
    dest: Path = typer.Option(
        Path.cwd() / EXAMPLE_NAME, "--dest", help="Where to copy the example repository."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite the destination."),
) -> None:
    """Unpack the bundled example repository -- a weak suite tia is meant to flag."""
    target = dest.expanduser().resolve()
    if target.exists():
        if not force:
            err_console.print(
                f"\n[bold red]Error:[/bold red] {target} already exists.\n\n"
                "[bold]Hint:[/bold] Pass --force to overwrite it, or choose another --dest."
            )
            raise typer.Exit(code=2)
        shutil.rmtree(target)
    copy_example(target)
    console.print(f"[green]Example repository written to[/green] {target}")
    console.print(
        f"\nTry it:\n"
        f"  [bold]cd {target}[/bold]\n"
        f"  [bold]pytest -q[/bold]            [dim]# green -- the suite passes[/dim]\n"
        f"  [bold]tia scan[/bold]             [dim]# and yet[/dim]"
    )


@app.command("clear-cache")
def clear_cache(
    repo: Path = typer.Option(Path.cwd(), "--repo", "-C", help="Repository whose cache to clear."),
) -> None:
    """Delete the on-disk LLM response cache for a repository."""
    try:
        config = Config.load(repo.expanduser().resolve())
    except TIAError as exc:
        _fail(exc)
        return
    from .cache import ResponseCache

    removed = ResponseCache(config.cache_path).clear()
    console.print(f"[green]Removed {removed} cached response(s)[/green] from {config.cache_path}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_agent(
    repo: Path,
    config_path: Path | None,
    workers: int | None,
    mutants_per_req: int | None,
    timeout: int | None,
    test_command: str | None,
    model: str | None,
    offline: bool,
    verbose: bool,
) -> Agent:
    return Agent.from_path(
        repo.expanduser().resolve(),
        config_path=config_path,
        workers=workers,
        mutants_per_req=mutants_per_req,
        timeout=timeout,
        test_command=test_command,
        model=model,
        offline=offline or None,
        verbose=verbose or None,
    )


def _print_preamble(config: Config) -> None:
    mode = "AST operators (offline)" if config.offline else config.model
    console.print(
        f"[bold]test-integrity-agent[/bold] [dim]{__version__}[/dim]\n"
        f"[dim]repo[/dim] {config.repo}\n"
        f"[dim]tests[/dim] {config.test_command}\n"
        f"[dim]model[/dim] {mode}   "
        f"[dim]mutants/req[/dim] {config.mutants_per_req}   "
        f"[dim]workers[/dim] {config.workers}"
    )


def _write_outputs(
    agent: Agent,
    report_obj: ScanReport,
    json_out: Path | None,
    html_out: Path | None,
) -> None:
    try:
        agent.config.cache_path.mkdir(parents=True, exist_ok=True)
        write_json(report_obj, agent.config.cache_path / LAST_REPORT_NAME)
    except OSError:
        pass
    console.print()
    if json_out:
        console.print(f"[green]JSON report:[/green] {write_json(report_obj, json_out)}")
    if html_out:
        console.print(f"[green]HTML report:[/green] {write_html(report_obj, html_out)}")
    if not json_out and not html_out:
        console.print(
            "[dim]Tip: `tia report --html report.html` renders this run as a "
            "shareable page.[/dim]"
        )


def _load_report(path: Path) -> ScanReport:
    """Rebuild a :class:`ScanReport` from JSON written by a previous scan."""
    import json

    from .errors import ConfigError
    from .models import (
        MutantOutcome,
        MutantResult,
        Mutation,
        MutationSource,
        Requirement,
        RequirementScore,
        SourceSpan,
    )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read the report at {path}: {exc}") from exc

    scores: list[RequirementScore] = []
    for entry in data.get("requirements", []):
        requirement = Requirement(
            id=entry["requirement"]["id"],
            text=entry["requirement"]["text"],
            source=entry["requirement"]["source"],
            tags=tuple(entry["requirement"].get("tags", [])),
        )
        results: list[MutantResult] = []
        for mutant in entry.get("mutants", []):
            span_data = mutant.get("span")
            span = (
                SourceSpan(
                    path=span_data["path"],
                    start_line=span_data["start_line"],
                    end_line=span_data["end_line"],
                    symbol=span_data.get("symbol"),
                    confidence=span_data.get("confidence", 1.0),
                    reason=span_data.get("reason", ""),
                )
                if span_data
                else None
            )
            mutation = Mutation(
                id=mutant["id"],
                requirement_id=mutant["requirement_id"],
                path=mutant["path"],
                original_source="",
                mutated_source="",
                explanation=mutant.get("explanation", ""),
                origin=MutationSource(mutant.get("origin", "llm")),
                operator=mutant.get("operator", ""),
                span=span,
            )
            results.append(
                MutantResult(
                    mutation=mutation,
                    outcome=MutantOutcome(mutant["outcome"]),
                    duration_s=mutant.get("duration_s", 0.0),
                    returncode=mutant.get("returncode"),
                    output_tail=mutant.get("output_tail", ""),
                    diff=mutant.get("diff", ""),
                    error=mutant.get("error", ""),
                )
            )
        score = RequirementScore(
            requirement=requirement,
            generated=entry.get("generated", len(results)),
            killed=entry.get("killed", 0),
            survived=entry.get("survived", 0),
            timed_out=entry.get("timed_out", 0),
            errored=entry.get("errored", 0),
            results=results,
            note=entry.get("note", ""),
        )
        scores.append(score)

    return ScanReport(
        repo=data.get("repo", ""),
        model=data.get("model", ""),
        started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at", ""),
        duration_s=data.get("duration_s", 0.0),
        scores=scores,
        llm_used=data.get("llm_used", True),
        degraded_reason=data.get("degraded_reason", ""),
        usage=data.get("usage", {}),
        estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
        test_command=data.get("test_command", ""),
    )


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except TIAError as exc:  # pragma: no cover - defence in depth
        err_console.print(f"\n[bold red]Error:[/bold red] {exc.message}")
        if exc.hint:
            err_console.print(f"\n[bold]Hint:[/bold] {exc.hint}")
        sys.exit(exc.exit_code)


if __name__ == "__main__":  # pragma: no cover
    main()
