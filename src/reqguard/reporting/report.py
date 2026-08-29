"""Write the report directory. Owned by Track C.

Reference: docs/03-implementation-manual.md section 12 (blueprint section 30).

    reqguard-report/
    ├── summary.html          Jinja2, self-contained, no external assets
    ├── findings.json         one Verdict per requirement
    ├── mutations.json        specs + diffs
    ├── metrics.json          evaluator output (when an evaluation ran)
    ├── trajectory.jsonl      (written by the trajectory logger during the run)
    ├── mutations/<req_id>.diff   the exact applied diff — reproducible
    └── repairs/<req_id>.py       the proposed test, UNAPPLIED

Repairs are written to ``repairs/<req_id>.py`` **in this directory, never to the
user's repository** — applying one is a separate, explicit command
(``reqguard apply``, section 13.3).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from reqguard.models import Verdict
from reqguard.reporting.formatter import render_terminal, summarize

__all__ = ["write_report", "REPORT_FILES"]

REPORT_FILES = ("summary.html", "findings.json", "mutations.json")

_TEMPLATES = Path(__file__).parent / "templates"


def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def write_report(
    verdicts: list[Verdict],
    output_dir: str | Path,
    *,
    repo: str = ".",
    metrics: dict[str, Any] | None = None,
    trajectory_path: str | Path | None = None,
) -> Path:
    """Materialize the full report directory and return its path."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "findings.json").write_text(
        json.dumps([v.model_dump(mode="json") for v in verdicts], indent=2),
        encoding="utf-8",
    )

    mutations = [
        {
            "requirement_id": v.requirement.id,
            "spec": v.mutation.model_dump(mode="json") if v.mutation else None,
            "diff": v.result.diff if v.result else None,
            "survived": v.result.survived if v.result else None,
        }
        for v in verdicts
        if v.mutation is not None
    ]
    (out / "mutations.json").write_text(json.dumps(mutations, indent=2), encoding="utf-8")

    # Per-requirement artifacts (Doc 3 section 12):
    #   mutations/<req_id>.diff  — the exact applied diff, reproducible
    #   repairs/<req_id>.py      — the proposed test, UNAPPLIED, in THIS dir only
    for v in verdicts:
        rid = v.requirement.id
        if v.result is not None and v.result.diff:
            diff_dir = out / "mutations"
            diff_dir.mkdir(parents=True, exist_ok=True)
            (diff_dir / f"{rid}.diff").write_text(v.result.diff, encoding="utf-8")
        if v.repair is not None and v.repair.test_source:
            repair_dir = out / "repairs"
            repair_dir.mkdir(parents=True, exist_ok=True)
            (repair_dir / f"{rid}.py").write_text(v.repair.test_source, encoding="utf-8")

    if metrics is not None:
        (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    if trajectory_path is not None and Path(trajectory_path).is_file():
        dst = out / "trajectory.jsonl"
        if Path(trajectory_path).resolve() != dst.resolve():
            shutil.copyfile(trajectory_path, dst)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = _jinja().get_template("summary.html.j2").render(
        verdicts=verdicts,
        s=summarize(verdicts),
        repo=repo,
        generated_at=generated_at,
        metrics=json.dumps(metrics, indent=2) if metrics else None,
    )
    (out / "summary.html").write_text(html, encoding="utf-8")

    (out / "summary.txt").write_text(
        render_terminal(verdicts, str(out) + "/"), encoding="utf-8"
    )
    return out
