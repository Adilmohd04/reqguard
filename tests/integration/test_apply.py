"""`reqguard apply` — the ONLY write path into the user's repository.

Doc 1 section 19 / Doc 3 section 13.3: the original repository is read-only
except for this one, explicit, confirmed command. Everything here exists to
verify that contract actually holds, not just that the happy path works.

Gate: pytest tests/integration/test_apply.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reqguard.cli.main import app

runner = CliRunner()


def _write_findings(report_dir: Path, *, test_path: str, verified: bool = True) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    finding = {
        "requirement": {
            "id": "REQ-01",
            "statement": "x",
            "behavior": {"precondition": "x", "prohibited_action": "x", "expected_result": "x"},
            "category": "business_rule",
            "severity": "high",
            "source_location": "requirements.md:L1",
        },
        "status": "REPAIRED" if verified else "INSUFFICIENTLY_PROTECTED",
        "repair": {
            "requirement_id": "REQ-01",
            "mutation_id": "m1",
            "test_path": test_path,
            "test_source": "def test_x():\n    assert True\n",
            "explanation": "x",
        },
        "verification": {
            "passes_on_original": verified,
            "kills_mutant": verified,
            "verified": verified,
        } if verified else None,
    }
    (report_dir / "findings.json").write_text(json.dumps([finding]), encoding="utf-8")


class TestApplyHappyPath:
    def test_writes_the_verified_repair(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path="tests/test_new.py")

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )

        assert result.exit_code == 0, result.output
        written = repo / "tests" / "test_new.py"
        assert written.is_file()
        assert written.read_text(encoding="utf-8") == "def test_x():\n    assert True\n"

    def test_declining_the_prompt_writes_nothing(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path="tests/test_new.py")

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01", "--report-dir", str(report_dir)],
            input="n\n",
        )

        assert result.exit_code != 0
        assert not (repo / "tests" / "test_new.py").exists()


class TestApplyRefusals:
    def test_refuses_when_no_report_exists(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(tmp_path / "no-such-report"), "--yes"],
        )
        assert result.exit_code != 0

    def test_refuses_unknown_requirement_id(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path="tests/test_new.py")

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-99",
             "--report-dir", str(report_dir), "--yes"],
        )
        assert result.exit_code != 0

    def test_refuses_an_unverified_repair(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path="tests/test_new.py", verified=False)

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )
        assert result.exit_code != 0
        assert not (repo / "tests" / "test_new.py").exists()

    def test_refuses_to_overwrite_an_existing_file(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / "tests").mkdir(parents=True)
        (repo / "tests" / "test_new.py").write_text("already here", encoding="utf-8")
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path="tests/test_new.py")

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )
        assert result.exit_code != 0
        assert (repo / "tests" / "test_new.py").read_text(encoding="utf-8") == "already here"


class TestApplyProve:
    """--prove: after writing the repair, actually run the repo's own suite to
    demonstrate the gap is closed on the REAL repo, and roll the write back if
    it isn't. This is the 'it solved it, not just reported it' path."""

    def _make_repo_with_impl(self, repo: Path) -> None:
        """A tiny but real, install-free repo whose suite passes."""
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "status.py").write_text(
            "def fulfillment_status(ordered, shipped):\n"
            "    if shipped == 0:\n"
            "        return 'pending'\n"
            "    if shipped >= ordered:\n"
            "        return 'completed'\n"
            "    return 'partial'\n",
            encoding="utf-8",
        )
        (repo / "tests").mkdir(parents=True)
        (repo / "tests" / "test_existing.py").write_text(
            "from status import fulfillment_status\n"
            "def test_completed():\n"
            "    assert fulfillment_status(100, 100) == 'completed'\n",
            encoding="utf-8",
        )
        (repo / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\npythonpath = ['src']\n", encoding="utf-8"
        )

    def _write_findings_with_source(
        self, report_dir: Path, *, test_path: str, test_source: str
    ) -> None:
        report_dir.mkdir(parents=True, exist_ok=True)
        finding = {
            "requirement": {
                "id": "REQ-01", "statement": "x",
                "behavior": {"precondition": "x", "prohibited_action": "x",
                             "expected_result": "x"},
                "category": "business_rule", "severity": "high",
                "source_location": "requirements.md:L1",
            },
            "status": "REPAIRED",
            "repair": {
                "requirement_id": "REQ-01", "mutation_id": "m1",
                "test_path": test_path, "test_source": test_source, "explanation": "x",
            },
            "verification": {"passes_on_original": True, "kills_mutant": True,
                             "verified": True},
        }
        (report_dir / "findings.json").write_text(json.dumps([finding]), encoding="utf-8")

    def test_prove_success_keeps_the_file_and_reports_green(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._make_repo_with_impl(repo)
        report_dir = tmp_path / "report"
        # A repair test that genuinely passes against the correct code: it pins
        # the partial-fulfillment boundary the existing suite never checks.
        repair_src = (
            "from status import fulfillment_status\n"
            "def test_partial_is_not_completed():\n"
            "    assert fulfillment_status(100, 80) == 'partial'\n"
        )
        self._write_findings_with_source(
            report_dir, test_path="tests/test_repair.py", test_source=repair_src
        )

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes", "--prove"],
        )

        assert result.exit_code == 0, result.output
        assert "repo suite green with the repair" in result.output
        assert (repo / "tests" / "test_repair.py").is_file()

    def test_prove_failure_rolls_back_and_leaves_repo_unchanged(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._make_repo_with_impl(repo)
        report_dir = tmp_path / "report"
        # A repair test that does NOT pass against the real repo (wrong
        # expectation) -- simulates a verified-in-sandbox result that fails to
        # reproduce here. --prove must roll it back.
        bad_src = (
            "from status import fulfillment_status\n"
            "def test_wrong_expectation():\n"
            "    assert fulfillment_status(100, 80) == 'completed'\n"
        )
        self._write_findings_with_source(
            report_dir, test_path="tests/test_repair.py", test_source=bad_src
        )

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes", "--prove"],
        )

        assert result.exit_code != 0
        # Rolled back: the file must not survive, and nothing else disturbed.
        assert not (repo / "tests" / "test_repair.py").exists()
        assert (repo / "tests" / "test_existing.py").is_file()
        assert "NOT kept" in result.output or "not green" in result.output

    def test_without_prove_does_not_run_the_suite(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._make_repo_with_impl(repo)
        report_dir = tmp_path / "report"
        bad_src = (
            "from status import fulfillment_status\n"
            "def test_wrong_expectation():\n"
            "    assert fulfillment_status(100, 80) == 'completed'\n"
        )
        self._write_findings_with_source(
            report_dir, test_path="tests/test_repair.py", test_source=bad_src
        )

        # No --prove: apply writes and returns without running the suite, so
        # even a repair that would fail the suite is written (old behavior).
        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )

        assert result.exit_code == 0, result.output
        assert (repo / "tests" / "test_repair.py").is_file()
        assert "repo suite" not in result.output


class TestApplyPathContainment:
    """The gap this file exists to close: test_path is model-authored prose,
    not a structurally-guaranteed-safe path. Doc 1 section 19's whole
    safety model collapses if any of these write outside the repo."""

    @pytest.mark.parametrize("escape_path", [
        "../outside.py",
        "../../outside.py",
        "tests/../../outside.py",
    ])
    def test_refuses_relative_traversal_out_of_the_repo(self, tmp_path, escape_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path=escape_path)

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )

        assert result.exit_code != 0
        assert not (tmp_path / "outside.py").exists()

    def test_refuses_an_absolute_path_outside_the_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        outside_target = tmp_path / "outside" / "evil.py"
        _write_findings(report_dir, test_path=str(outside_target))

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )

        assert result.exit_code != 0
        assert not outside_target.exists()

    def test_allows_a_nested_but_contained_path(self, tmp_path):
        """The containment check must not be so strict it blocks a
        legitimate nested destination -- only escapes are refused."""
        repo = tmp_path / "repo"
        repo.mkdir()
        report_dir = tmp_path / "report"
        _write_findings(report_dir, test_path="tests/nested/deep/test_new.py")

        result = runner.invoke(
            app,
            ["apply", str(repo), "--requirement", "REQ-01",
             "--report-dir", str(report_dir), "--yes"],
        )

        assert result.exit_code == 0, result.output
        assert (repo / "tests" / "nested" / "deep" / "test_new.py").is_file()
