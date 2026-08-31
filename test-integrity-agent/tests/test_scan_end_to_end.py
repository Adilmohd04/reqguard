"""The whole pipeline, the scoring rules, the renderers, and the CLI.

These run offline (AST operators), so the suite never needs an API key and never
makes a network call -- but everything else is real: real sandboxes, real
subprocesses, real pass/fail signal from a real test suite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from test_integrity_agent import Agent
from test_integrity_agent.cli import app
from test_integrity_agent.config import Config
from test_integrity_agent.errors import MissingAPIKeyError
from test_integrity_agent.models import (
    MutantOutcome,
    MutantResult,
    Mutation,
    MutationSource,
    Requirement,
)
from test_integrity_agent.report import render_html, to_json
from test_integrity_agent.scorer import build_report, score_requirement, verdict

runner = CliRunner()
PYTEST_CMD = f'"{sys.executable}" -m pytest -q'


def output_of(result) -> str:
    """Everything the CLI printed. Errors go to stderr, which Click keeps apart."""
    text = result.output or ""
    try:
        text += result.stderr or ""
    except (ValueError, AttributeError):
        pass
    return text


def agent_for(repo: Path, **overrides: object) -> Agent:
    return Agent.from_path(repo, test_command=PYTEST_CMD, offline=True, **overrides)


# --- scoring ----------------------------------------------------------------


def make_result(outcome: MutantOutcome) -> MutantResult:
    mutation = Mutation(
        id="m",
        requirement_id="REQ-001",
        path="p.py",
        original_source="",
        mutated_source="",
        explanation="",
        origin=MutationSource.AST,
    )
    return MutantResult(mutation=mutation, outcome=outcome, duration_s=0.1)


REQ = Requirement(id="REQ-001", text="A rule.", source="x")


def test_score_excludes_timeouts_and_errors_from_the_denominator() -> None:
    score = score_requirement(
        REQ,
        [
            make_result(MutantOutcome.KILLED),
            make_result(MutantOutcome.SURVIVED),
            make_result(MutantOutcome.TIMEOUT),
            make_result(MutantOutcome.ERROR),
        ],
    )
    assert (score.killed, score.survived, score.timed_out, score.errored) == (1, 1, 1, 1)
    assert score.evaluable == 2
    assert score.score == pytest.approx(0.5)
    assert score.protected is False
    assert verdict(score) == "PARTIAL"


def test_a_requirement_with_no_signal_is_inconclusive_not_perfect() -> None:
    score = score_requirement(REQ, [make_result(MutantOutcome.TIMEOUT)])
    assert score.score is None
    assert score.protected is False
    assert verdict(score) == "INCONCLUSIVE"
    assert "usable pass/fail signal" in score.note


def test_a_fully_defended_requirement_is_protected() -> None:
    score = score_requirement(REQ, [make_result(MutantOutcome.KILLED)] * 3)
    assert score.score == 1.0
    assert verdict(score) == "PROTECTED"


def test_an_undefended_requirement_is_unprotected() -> None:
    score = score_requirement(REQ, [make_result(MutantOutcome.SURVIVED)] * 2)
    assert score.score == 0.0
    assert verdict(score) == "UNPROTECTED"
    assert len(score.survivors) == 2


def test_zero_generated_mutations_is_noted() -> None:
    score = score_requirement(REQ, [], generated=0)
    assert verdict(score) == "INCONCLUSIVE"
    assert "no mutation could be generated" in score.note


def test_suite_score_aggregates_across_requirements() -> None:
    good = score_requirement(REQ, [make_result(MutantOutcome.KILLED)] * 3)
    bad = score_requirement(
        Requirement(id="REQ-002", text="Another.", source="x"),
        [make_result(MutantOutcome.SURVIVED)],
    )
    report = build_report(
        repo="/r", model="m", scores=[good, bad], started_at="", finished_at="",
        duration_s=1.0, test_command="pytest", llm_used=False,
    )
    assert report.suite_score == pytest.approx(0.75)
    assert [s.requirement.id for s in report.unprotected] == ["REQ-002"]


# --- renderers --------------------------------------------------------------


def test_json_report_round_trips_through_the_schema() -> None:
    score = score_requirement(REQ, [make_result(MutantOutcome.SURVIVED)])
    report = build_report(
        repo="/r", model="claude-sonnet-4-6", scores=[score], started_at="a",
        finished_at="b", duration_s=2.0, test_command="pytest -q", llm_used=True,
    )
    data = json.loads(to_json(report))
    assert data["schema_version"] == 1
    assert data["totals"]["survived"] == 1
    assert data["requirements"][0]["requirement"]["id"] == "REQ-001"


def test_html_report_is_self_contained_and_escapes_content() -> None:
    requirement = Requirement(id="REQ-001", text="Never <script>alert(1)</script>.", source="x")
    score = score_requirement(requirement, [make_result(MutantOutcome.SURVIVED)])
    report = build_report(
        repo="/r", model="m", scores=[score], started_at="a", finished_at="b",
        duration_s=1.0, test_command="pytest", llm_used=False,
    )
    html = render_html(report)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "http://" not in html and "https://" not in html  # no external assets
    assert "UNPROTECTED" in html


# --- the pipeline -----------------------------------------------------------


@pytest.mark.timeout(600)
def test_offline_scan_of_the_example_repo_finds_the_gaps(example_repo: Path) -> None:
    report = agent_for(example_repo, mutants_per_req=2, workers=2).scan()

    assert len(report.scores) == 5
    assert report.total_generated > 0
    assert report.llm_used is False
    assert "offline" in report.degraded_reason

    # The suite defends something...
    assert report.total_killed > 0
    # ...and misses something. The example is built so both are true.
    assert report.total_survived > 0
    assert report.suite_score is not None and 0.0 < report.suite_score < 1.0

    unprotected_ids = {s.requirement.id for s in report.unprotected}
    assert unprotected_ids, "expected at least one requirement with a survivor"

    for score in report.scores:
        for result in score.results:
            assert result.mutation.requirement_id == score.requirement.id
            if result.survived:
                assert result.diff.startswith("---")


@pytest.mark.timeout(300)
def test_scan_can_target_a_single_requirement(example_repo: Path) -> None:
    report = agent_for(example_repo, mutants_per_req=1, workers=1).scan(only=["REQ-005"])
    assert [s.requirement.id for s in report.scores] == ["REQ-005"]


@pytest.mark.timeout(300)
def test_scan_leaves_the_repository_untouched(example_repo: Path) -> None:
    source = example_repo / "src" / "shop" / "orders.py"
    before = source.read_text(encoding="utf-8")
    agent_for(example_repo, mutants_per_req=1, workers=2).scan()
    assert source.read_text(encoding="utf-8") == before


def test_scan_without_a_key_and_without_offline_is_a_clear_error(
    tiny_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent = Agent.from_path(tiny_repo, test_command=PYTEST_CMD)
    with pytest.raises(MissingAPIKeyError) as exc:
        agent.scan()
    assert "ANTHROPIC_API_KEY" in exc.value.message
    assert "--offline" in (exc.value.hint or "")


def test_from_path_rejects_an_unknown_override(tiny_repo: Path) -> None:
    from test_integrity_agent.errors import TIAError

    with pytest.raises(TIAError, match="Unknown configuration override"):
        Agent.from_path(tiny_repo, nonsense=1)


# --- CLI --------------------------------------------------------------------


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "test-integrity-agent" in output_of(result)


def test_cli_init_writes_a_loadable_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "tia.yaml").is_file()
    assert Config.load(tmp_path).model == "claude-sonnet-4-6"

    again = runner.invoke(app, ["init", "--repo", str(tmp_path)])
    assert again.exit_code == 2
    assert "--force" in output_of(again)


def test_cli_requirements_lists_ids(example_repo: Path) -> None:
    result = runner.invoke(app, ["requirements", "--repo", str(example_repo)])
    assert result.exit_code == 0
    assert "REQ-001" in output_of(result)
    assert "REQ-005" in output_of(result)


def test_cli_example_unpacks_a_runnable_repo(tmp_path: Path) -> None:
    dest = tmp_path / "demo"
    result = runner.invoke(app, ["example", "--dest", str(dest)])
    assert result.exit_code == 0
    assert (dest / "REQUIREMENTS.md").is_file()
    assert (dest / "tia.yaml").is_file()
    assert (dest / "src" / "shop" / "orders.py").is_file()

    again = runner.invoke(app, ["example", "--dest", str(dest)])
    assert again.exit_code == 2


def test_cli_scan_on_a_bad_config_exits_two(tmp_path: Path) -> None:
    (tmp_path / "tia.yaml").write_text("workers: 0\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", "--repo", str(tmp_path)])
    assert result.exit_code == 2


def test_cli_scan_without_a_key_exits_three(tiny_repo: Path) -> None:
    result = runner.invoke(
        app, ["scan", "--repo", str(tiny_repo), "--test-command", PYTEST_CMD]
    )
    assert result.exit_code == 3
    assert "ANTHROPIC_API_KEY" in output_of(result)


@pytest.mark.timeout(600)
def test_cli_scan_offline_writes_reports_and_signals_survivors(
    example_repo: Path, tmp_path: Path
) -> None:
    html_out = tmp_path / "report.html"
    json_out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "scan",
            "--repo", str(example_repo),
            "--offline",
            "--mutants-per-req", "1",
            "--workers", "2",
            "--test-command", PYTEST_CMD,
            "--html", str(html_out),
            "--json", str(json_out),
        ],
    )
    assert result.exit_code in (0, 1), output_of(result)
    assert html_out.is_file()
    assert json_out.is_file()
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert data["totals"]["requirements"] == 5
    assert "Suite integrity" in output_of(result)

    # And the saved run can be re-rendered without re-running anything.
    rerender = tmp_path / "again.html"
    again = runner.invoke(
        app, ["report", "--repo", str(example_repo), "--html", str(rerender)]
    )
    assert again.exit_code == 0
    assert rerender.is_file()


def test_cli_report_without_a_previous_scan_exits_two(tiny_repo: Path) -> None:
    result = runner.invoke(app, ["report", "--repo", str(tiny_repo)])
    assert result.exit_code == 2
    assert "No saved report" in output_of(result)
