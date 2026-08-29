"""`reqguard trajectory` — the representative narrative Doc 1 section 27
describes, rendered from an existing trajectory.jsonl.

This is a read-only consumer of reqguard.tracing.trajectory.read_events() —
these tests write raw JSONL fixtures directly rather than going through the
Trajectory class, since constructing arbitrary event sequences (multiple
requirements, api_call cost data no replay run ever produces, an
orchestration-level event with no requirement_id) is more direct than
driving a real analyze() run for each shape.

Gate: pytest tests/integration/test_trajectory_cli.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reqguard.cli.main import app
from reqguard.cli._runner import render_trajectory

runner = CliRunner()


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + ("\n" if events else ""),
        encoding="utf-8",
    )


REQ01_EVENTS = [
    {"type": "tool_result", "step": 1, "requirement_id": "REQ-01", "stage": "execute",
     "tool": "run_tests", "result_summary": "baseline: 2 passed, 0 failed, exit 0"},
    {"type": "decision", "step": 2, "requirement_id": "REQ-01", "stage": "investigate",
     "rationale": "Selected ship_order because it contains the only guard."},
    {"type": "decision", "step": 3, "requirement_id": "REQ-01", "stage": "plan",
     "rationale": "Deleting the two guard lines is the smallest plausible change."},
    {"type": "verdict", "step": 4, "requirement_id": "REQ-01", "stage": "execute",
     "payload": {"survived": True}},
    {"type": "decision", "step": 5, "requirement_id": "REQ-01", "stage": "repair",
     "rationale": "pytest.raises(...) detects the missing guard."},
    {"type": "verdict", "step": 6, "requirement_id": "REQ-01", "stage": "repair",
     "payload": {"verified": True, "passes_on_original": True, "kills_mutant": True}},
]


class TestRenderTrajectory:
    def test_renders_the_full_arc_in_text_format(self, tmp_path):
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, REQ01_EVENTS)

        out = render_trajectory(traj, fmt="text")

        assert "REQ-01" in out
        assert "baseline: 2 passed" in out
        assert "Selected ship_order" in out
        assert "Decision: Deleting the two guard lines" in out
        assert "Verdict: survived=True" in out
        assert "kills_mutant=True" in out

    def test_renders_markdown_with_headers_and_bold_decisions(self, tmp_path):
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, REQ01_EVENTS)

        out = render_trajectory(traj, fmt="md")

        assert "## REQ-01" in out
        assert "**[investigate] Decision:**" in out
        assert "**[repair] Verdict:**" in out
        assert "## Cost" in out

    def test_accepts_a_directory_and_finds_trajectory_jsonl_inside(self, tmp_path):
        report_dir = tmp_path / "reqguard-report"
        _write_jsonl(report_dir / "trajectory.jsonl", REQ01_EVENTS)

        out = render_trajectory(report_dir, fmt="text")
        assert "REQ-01" in out

    def test_multiple_requirements_group_separately_in_first_seen_order(self, tmp_path):
        events = [
            {"type": "decision", "step": 1, "requirement_id": "REQ-02", "stage": "plan",
             "rationale": "second requirement's own decision"},
            {"type": "decision", "step": 2, "requirement_id": "REQ-01", "stage": "plan",
             "rationale": "first requirement's own decision"},
        ]
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, events)

        out = render_trajectory(traj, fmt="text")

        # REQ-02 appeared first in the file, so its heading comes first,
        # even though REQ-01 sorts first alphabetically.
        assert out.index("REQ-02") < out.index("REQ-01")
        assert "second requirement's own decision" in out
        assert "first requirement's own decision" in out

    def test_events_with_no_requirement_id_get_their_own_trailing_section(self, tmp_path):
        events = [
            {"type": "decision", "step": 1, "requirement_id": "REQ-01", "stage": "plan",
             "rationale": "attributed decision"},
            {"type": "error", "step": 2, "requirement_id": None, "stage": None,
             "kind": "setup_failure", "message": "something orchestration-level failed"},
        ]
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, events)

        out = render_trajectory(traj, fmt="text")

        assert "orchestration" in out.lower()
        assert "setup_failure" in out
        # the unattributed section trails the attributed one
        assert out.index("REQ-01") < out.index("orchestration")

    def test_cost_is_summed_from_api_call_records_not_estimated(self, tmp_path):
        events = [
            {"type": "api_call", "step": 1, "requirement_id": "REQ-01", "stage": "plan",
             "model": "claude-opus-5", "request_id": "req_1",
             "usage": {"input_tokens": 1000, "output_tokens": 200,
                       "cache_read_input_tokens": 500, "cache_creation_input_tokens": 0}},
            {"type": "api_call", "step": 2, "requirement_id": "REQ-01", "stage": "repair",
             "model": "claude-opus-5", "request_id": "req_2",
             "usage": {"input_tokens": 300, "output_tokens": 50,
                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 100}},
        ]
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, events)

        out = render_trajectory(traj, fmt="text")

        assert "api_calls=2" in out
        assert "input_tokens=1300" in out
        assert "output_tokens=250" in out
        assert "cache_read=500" in out
        assert "cache_creation=100" in out
        # api_call events are folded into the summary, not narrated per-line
        assert "req_1" not in out

    def test_empty_trajectory_is_a_clean_message_not_a_crash(self, tmp_path):
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, [])

        out = render_trajectory(traj, fmt="text")
        assert "no events recorded" in out

    def test_missing_path_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            render_trajectory(tmp_path / "does-not-exist.jsonl")

    def test_directory_with_no_trajectory_file_raises_file_not_found(self, tmp_path):
        empty_dir = tmp_path / "empty-report"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            render_trajectory(empty_dir)


class TestTrajectoryCommand:
    """The actual `reqguard trajectory` CLI surface."""

    def test_renders_successfully_exit_zero(self, tmp_path):
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, REQ01_EVENTS)

        result = runner.invoke(app, ["trajectory", str(traj)])
        assert result.exit_code == 0, result.output
        assert "REQ-01" in result.output

    def test_md_format_flag(self, tmp_path):
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, REQ01_EVENTS)

        result = runner.invoke(app, ["trajectory", str(traj), "--format", "md"])
        assert result.exit_code == 0, result.output
        assert "## REQ-01" in result.output

    def test_missing_file_exits_nonzero_with_clean_message(self, tmp_path):
        result = runner.invoke(app, ["trajectory", str(tmp_path / "nope.jsonl")])
        assert result.exit_code != 0
        assert "Traceback" not in result.output

    def test_invalid_format_exits_nonzero(self, tmp_path):
        traj = tmp_path / "trajectory.jsonl"
        _write_jsonl(traj, REQ01_EVENTS)

        result = runner.invoke(app, ["trajectory", str(traj), "--format", "yaml"])
        assert result.exit_code != 0
