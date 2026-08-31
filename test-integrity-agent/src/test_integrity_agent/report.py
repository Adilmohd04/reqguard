"""Render a :class:`~test_integrity_agent.models.ScanReport` three ways.

Terminal (rich), JSON (for CI and diffing between runs), and a self-contained
HTML file (no CDN, no external assets, works from ``file://``).

Every renderer leads with the same thing: the survived mutants. A survived
mutant is the whole point of the tool -- it is a concrete, executed proof that
the code can be broken in a specific way without a single test noticing.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .models import MutationSource, RequirementScore, ScanReport
from .scorer import suspicious_all_survived, verdict

VERDICT_STYLE = {
    "PROTECTED": "bold green",
    "PARTIAL": "bold yellow",
    "UNPROTECTED": "bold red",
    "INCONCLUSIVE": "dim",
}


def format_score(score: float | None) -> str:
    """A percentage, or a dash when there was no signal to score."""
    return "-" if score is None else f"{score * 100:.0f}%"


def _score_style(score: float | None) -> str:
    if score is None:
        return "dim"
    if score >= 0.999:
        return "green"
    if score >= 0.5:
        return "yellow"
    return "red"


def _truncate(text: str, limit: int = 96) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------


def render_terminal(report: ScanReport, console: Console, show_all: bool = False) -> None:
    """Print the human-facing summary."""
    console.print()
    table = Table(
        title="Requirement integrity",
        title_style="bold",
        header_style="bold",
        show_lines=False,
        expand=True,
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Requirement", overflow="fold")
    table.add_column("Verdict", no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("K/S/T/E", justify="right", no_wrap=True)

    for score in report.scores:
        state = verdict(score)
        table.add_row(
            score.requirement.id,
            _truncate(score.requirement.text),
            Text(state, style=VERDICT_STYLE.get(state, "")),
            Text(format_score(score.score), style=_score_style(score.score)),
            f"{score.killed}/{score.survived}/{score.timed_out}/{score.errored}",
        )
    console.print(table)
    console.print(
        "[dim]K/S/T/E = killed / survived / timed out / errored. "
        "Score = killed / (killed + survived).[/dim]"
    )

    survivors = [s for s in report.scores if s.survived]
    if survivors:
        console.print()
        console.rule("[bold red]Survived mutants[/bold red]", characters="-")
        console.print(
            "[dim]Each block below is a change that broke a requirement while the "
            "test suite stayed green.[/dim]\n"
        )
        for score in report.unprotected:
            _render_survivors(score, console, show_all=show_all)

    _render_footer(report, console)


def _render_survivors(score: RequirementScore, console: Console, show_all: bool) -> None:
    survivors = score.survivors
    shown = survivors if show_all else survivors[:2]
    console.print(
        Panel(
            Text(score.requirement.text, style="bold"),
            title=f"[cyan]{score.requirement.id}[/cyan] - "
            f"{score.survived} of {score.evaluable} mutants survived",
            title_align="left",
            border_style="red",
        )
    )
    for result in shown:
        origin = result.mutation.origin
        tag = "requirement-directed" if origin is MutationSource.LLM else "AST operator"
        console.print(f"  [dim]{result.mutation.path} - {tag}[/dim]")
        console.print(f"  [yellow]{_truncate(result.mutation.explanation, 160)}[/yellow]")
        diff = result.diff.strip()
        if diff:
            console.print(
                Syntax(diff, "diff", theme="ansi_dark", background_color="default", word_wrap=True)
            )
        console.print()
    hidden = len(survivors) - len(shown)
    if hidden > 0:
        console.print(f"  [dim]... and {hidden} more (use --show-all, or the HTML report)[/dim]\n")


def _render_footer(report: ScanReport, console: Console) -> None:
    console.rule(characters="-")
    suite = format_score(report.suite_score)
    style = _score_style(report.suite_score)
    console.print(
        f"[bold]Suite integrity: [{style}]{suite}[/{style}][/bold]  "
        f"({report.total_killed} killed, {report.total_survived} survived, "
        f"{report.total_timed_out} timed out, {report.total_errored} errored "
        f"across {len(report.scores)} requirements)"
    )
    console.print(
        f"[dim]{report.total_generated} mutants in {report.duration_s:.1f}s "
        f"using {report.model}[/dim]"
    )
    if report.usage:
        console.print(
            f"[dim]LLM: {report.usage.get('calls', 0)} calls "
            f"({report.usage.get('cached_calls', 0)} served from disk cache), "
            f"~${report.estimated_cost_usd:.3f} estimated[/dim]"
        )
    if report.degraded_reason:
        console.print(f"[yellow]Degraded: {report.degraded_reason}[/yellow]")
    if suspicious_all_survived(report):
        console.print(
            "[bold yellow]Warning:[/bold yellow] every mutant survived. That is "
            "possible, but it more often means the tests import an installed copy "
            "of the package instead of the code under `source_paths`, or that "
            "`test_command` does not exercise it at all."
        )
    if report.total_survived == 0 and report.total_killed > 0:
        console.print("[green]Every requirement with a mutant was defended.[/green]")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def write_json(report: ScanReport, path: Path) -> Path:
    """Write the machine-readable report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def to_json(report: ScanReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #16181d; --muted: #5b6472; --line: #e3e6eb;
  --card: #f7f8fa; --green: #1a7f45; --red: #c0392b; --amber: #9a6700;
  --code: #f2f4f7; --add: #e6ffed; --del: #ffeef0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115; --fg: #e6e8eb; --muted: #98a2b3; --line: #262b33;
    --card: #171a21; --green: #4ade80; --red: #f87171; --amber: #fbbf24;
    --code: #12151a; --add: #10281a; --del: #2b1416;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 40px 24px 80px; }
h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 18px; margin: 40px 0 12px; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 28px; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 18px; min-width: 132px; flex: 1;
}
.card .n { font-size: 26px; font-weight: 650; letter-spacing: -0.02em; }
.card .l { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
td.num, th.num { text-align: right; white-space: nowrap; }
.pill { font-size: 11px; font-weight: 650; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
.PROTECTED { background: color-mix(in srgb, var(--green) 18%, transparent); color: var(--green); }
.PARTIAL { background: color-mix(in srgb, var(--amber) 20%, transparent); color: var(--amber); }
.UNPROTECTED { background: color-mix(in srgb, var(--red) 18%, transparent); color: var(--red); }
.INCONCLUSIVE { background: var(--card); color: var(--muted); }
.mono, code, pre { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }
.survivor { border: 1px solid var(--line); border-left: 3px solid var(--red);
  border-radius: 8px; padding: 16px 18px; margin: 14px 0; background: var(--card); }
.survivor h3 { margin: 0 0 4px; font-size: 15px; }
.survivor .why { color: var(--amber); font-size: 13px; margin: 8px 0; }
.survivor .where { color: var(--muted); font-size: 12px; }
pre.diff {
  background: var(--code); border: 1px solid var(--line); border-radius: 6px;
  padding: 12px; overflow-x: auto; font-size: 12.5px; line-height: 1.45; margin: 10px 0 0;
}
pre.diff .a { background: var(--add); display: block; }
pre.diff .d { background: var(--del); display: block; }
pre.diff .h { color: var(--muted); display: block; }
.note { color: var(--muted); font-size: 13px; }
.warn { border: 1px solid var(--amber); background: color-mix(in srgb, var(--amber) 10%, transparent);
  border-radius: 8px; padding: 12px 16px; margin: 16px 0; font-size: 14px; }
footer { margin-top: 48px; color: var(--muted); font-size: 12px;
  border-top: 1px solid var(--line); padding-top: 16px; }
"""


def _diff_html(diff: str) -> str:
    rows = []
    for line in diff.splitlines():
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            rows.append(f'<span class="h">{escaped}</span>')
        elif line.startswith("+"):
            rows.append(f'<span class="a">{escaped}</span>')
        elif line.startswith("-"):
            rows.append(f'<span class="d">{escaped}</span>')
        else:
            rows.append(f"<span>{escaped}</span>")
    return "".join(rows) or "<span>(no diff captured)</span>"


def render_html(report: ScanReport) -> str:
    """Build the standalone HTML report as a string."""
    data: dict[str, Any] = report.to_dict()
    totals = data["totals"]
    suite = format_score(report.suite_score)

    rows = []
    for score in report.scores:
        state = verdict(score)
        rows.append(
            "<tr>"
            f'<td class="mono">{html.escape(score.requirement.id)}</td>'
            f"<td>{html.escape(score.requirement.text)}"
            + (f'<div class="note">{html.escape(score.note)}</div>' if score.note else "")
            + f'<div class="note">{html.escape(score.requirement.source)}</div></td>'
            f'<td><span class="pill {state}">{state}</span></td>'
            f'<td class="num">{format_score(score.score)}</td>'
            f'<td class="num mono">{score.killed}/{score.survived}/'
            f"{score.timed_out}/{score.errored}</td>"
            "</tr>"
        )

    survivor_blocks = []
    for score in report.unprotected:
        for result in score.survivors:
            origin = (
                "requirement-directed mutation"
                if result.mutation.origin is MutationSource.LLM
                else f"AST operator: {result.mutation.operator}"
            )
            survivor_blocks.append(
                '<div class="survivor">'
                f"<h3>{html.escape(score.requirement.id)} — survived</h3>"
                f"<div>{html.escape(score.requirement.text)}</div>"
                f'<div class="why">{html.escape(result.mutation.explanation)}</div>'
                f'<div class="where mono">{html.escape(result.mutation.path)}'
                + (
                    f" · {html.escape(result.mutation.span.symbol or '')}"
                    if result.mutation.span and result.mutation.span.symbol
                    else ""
                )
                + f" · {html.escape(origin)}</div>"
                f'<pre class="diff">{_diff_html(result.diff)}</pre>'
                "</div>"
            )

    warning = ""
    if suspicious_all_survived(report):
        warning = (
            '<div class="warn"><strong>Every mutant survived.</strong> That is '
            "possible, but it more often means the tests import an installed copy of "
            "the package rather than the code under <code>source_paths</code>, or that "
            "the test command does not exercise it.</div>"
        )
    if report.degraded_reason:
        warning += f'<div class="warn">Degraded run: {html.escape(report.degraded_reason)}</div>'

    survivors_section = (
        f"<h2>Survived mutants ({report.total_survived})</h2>"
        '<p class="note">Each block is a change that broke a requirement while the '
        "test suite stayed green. These are the gaps.</p>" + "".join(survivor_blocks)
        if survivor_blocks
        else '<h2>Survived mutants</h2><p class="note">None. Every evaluable mutant '
        "was caught by the suite.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test integrity report — {html.escape(Path(report.repo).name)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>Test integrity report</h1>
  <div class="sub mono">{html.escape(report.repo)} · {html.escape(report.model)} ·
    {html.escape(report.finished_at)} · {report.duration_s:.1f}s ·
    <code>{html.escape(report.test_command)}</code></div>
  {warning}
  <div class="cards">
    <div class="card"><div class="n">{suite}</div><div class="l">Suite integrity</div></div>
    <div class="card"><div class="n">{totals["requirements"]}</div><div class="l">Requirements</div></div>
    <div class="card"><div class="n">{totals["generated"]}</div><div class="l">Mutants</div></div>
    <div class="card"><div class="n">{totals["killed"]}</div><div class="l">Killed</div></div>
    <div class="card"><div class="n">{totals["survived"]}</div><div class="l">Survived</div></div>
  </div>
  <h2>Requirements</h2>
  <div class="table-scroll">
  <table>
    <thead><tr><th>ID</th><th>Requirement</th><th>Verdict</th>
      <th class="num">Score</th><th class="num">K/S/T/E</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  </div>
  <p class="note">K/S/T/E = killed / survived / timed out / errored.
     Score = killed ÷ (killed + survived); timeouts and errors are excluded because
     neither is evidence that a test asserted anything.</p>
  {survivors_section}
  <footer>
    Generated by test-integrity-agent ·
    {report.usage.get("calls", 0)} model calls
    ({report.usage.get("cached_calls", 0)} from cache) ·
    estimated ${report.estimated_cost_usd:.3f}
  </footer>
</div>
</body>
</html>
"""


def write_html(report: ScanReport, path: Path) -> Path:
    """Write the standalone HTML report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report), encoding="utf-8")
    return path
