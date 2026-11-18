"""ReqGuard as an MCP server, so Kiro, Codex, Claude Code, or any MCP client can use it.

Transport: stdio, newline-delimited JSON-RPC 2.0, per the Model Context Protocol.
Implemented with the stdlib only. No new dependency, and nothing listens on a
network port: the IDE launches this process and talks to it over pipes.

    python -m reqguard.integrations.mcp_server      # or: reqguard-mcp

Design rules:

* **stdout carries protocol messages only.** Logs go to stderr. The heavy
  pipeline (`reqguard analyze`) runs in a *subprocess* with its own captured
  output, so nothing it prints can corrupt the JSON-RPC stream.
* **Long runs don't block the client.** MCP clients time out tool calls (Codex
  defaults to 60s). `reqguard_analyze` starts a background run, waits up to
  `wait_seconds`, then returns either the finished result or a `run_id` for
  `reqguard_status` to poll.
* **The only repo write goes through `reqguard apply`.** That path refuses
  unverified repairs, refuses to overwrite, and checks path containment. On
  top of that, the tool also demands an explicit `confirm: true`, which the
  calling agent may set only after the human approves (hackathon rule 04).
* **No secrets cross this boundary.** `reqguard_check_setup` reports *which*
  provider key names are set, never their values.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["main", "TOOLS", "handle_message"]

SERVER_NAME = "reqguard"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PROVIDER_KEY_NAMES = ("OPENROUTER_API_KEY", "CONDUIT_API_KEY", "AEROLINK_API_KEY")

_STATUS_ORDER = {"INSUFFICIENTLY_PROTECTED": 0, "REPAIRED": 1, "INCONCLUSIVE": 2, "PROTECTED": 3}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class ToolError(Exception):
    """A user-facing tool failure. Returned as an MCP tool result with isError=true."""


def _log(msg: str) -> None:
    print(f"[reqguard-mcp] {msg}", file=sys.stderr, flush=True)


# ── shared helpers ────────────────────────────────────────────────────────────

def _repo(args: dict[str, Any]) -> Path:
    raw = args.get("repo")
    if not raw or not isinstance(raw, str):
        raise ToolError("`repo` is required: the absolute path of the project to check.")
    repo = Path(raw).expanduser().resolve()
    if not repo.is_dir():
        raise ToolError(f"repo is not a directory: {repo}")
    return repo


def _config(repo: Path):
    from reqguard.config import load_config
    from reqguard.errors import ConfigError

    try:
        return load_config(repo)
    except ConfigError as exc:
        raise ToolError(f"reqguard.yaml is invalid: {exc}") from exc


def _report_dir(repo: Path, cfg) -> Path:
    d = Path(cfg.report.output_dir)
    return d if d.is_absolute() else (repo / d).resolve()


def _cli(*argv: str) -> list[str]:
    """Invoke the ReqGuard CLI with the same interpreter this server runs under."""
    return [sys.executable, "-m", "reqguard.cli.main", *argv]


def _child_env(repo: Path) -> dict[str, str]:
    """Environment for a CLI child: the server's env plus the *project's* .env.

    The CLI's own `load_dotenv()` searches upward from its install location
    (site-packages for an installed ReqGuard), so it would never find the
    user's project .env. Load it here instead. Real environment variables
    always win over the file.
    """
    env = os.environ.copy()
    dotenv = repo / ".env"
    if dotenv.is_file():
        try:
            from dotenv import dotenv_values

            for key, value in dotenv_values(dotenv).items():
                if value is not None and key not in env:
                    env[key] = value
        except ImportError:  # pragma: no cover - python-dotenv is a dependency
            pass
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def _dotenv_keys(repo: Path) -> set[str]:
    """Key *names* with non-empty values in repo/.env. Values are never read out."""
    path = repo / ".env"
    names: set[str] = set()
    if not path.is_file():
        return names
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            if value.strip().strip("'\""):
                names.add(key.strip())
    except OSError:
        pass
    return names


# ── findings summarization (pure; reads findings.json) ────────────────────────

def summarize_findings(report_dir: Path, requirement: str | None = None, detail: bool = False) -> dict:
    path = report_dir / "findings.json"
    if not path.is_file():
        raise ToolError(f"no findings at {path}. Run reqguard_analyze first.")
    data = json.loads(path.read_text(encoding="utf-8"))

    items: list[dict] = []
    counts: dict[str, int] = {}
    for f in data:
        req = f.get("requirement") or {}
        rid = req.get("id")
        if requirement and rid != requirement:
            continue
        status = f.get("status", "INCONCLUSIVE")
        counts[status] = counts.get(status, 0) + 1

        item: dict[str, Any] = {
            "id": rid,
            "statement": req.get("statement"),
            "severity": req.get("severity"),
            "category": req.get("category"),
            "status": status,
        }
        if status == "INCONCLUSIVE":
            item["inconclusive_reason"] = f.get("inconclusive_reason")

        mut = f.get("mutation") or {}
        if mut:
            item["mutation"] = {
                "description": mut.get("description"),
                "expected_violation": mut.get("expected_violation"),
                "target": f"{mut.get('target_file')}::{mut.get('target_symbol')}",
            }
            if detail:
                item["mutation"]["violation_scenario"] = mut.get("violation_scenario")
                item["mutation"]["diff"] = (f.get("result") or {}).get("diff")

        rep = f.get("repair") or {}
        ver = f.get("verification") or {}
        if rep:
            item["repair"] = {
                "test_path": rep.get("test_path"),
                "verified": bool(ver.get("verified")),
                "passes_on_original": ver.get("passes_on_original"),
                "kills_mutant": ver.get("kills_mutant"),
            }
            if ver.get("failure_reason"):
                item["repair"]["failure_reason"] = ver.get("failure_reason")
            if detail:
                item["repair"]["explanation"] = rep.get("explanation")
                item["repair"]["test_source"] = rep.get("test_source")
        items.append(item)

    if requirement and not items:
        raise ToolError(f"{requirement} is not in {path}")

    items.sort(key=lambda i: (_STATUS_ORDER.get(i["status"], 9), _SEVERITY_ORDER.get(i["severity"] or "", 9)))

    next_steps: list[str] = []
    repaired = [i["id"] for i in items if i["status"] == "REPAIRED"]
    if repaired:
        next_steps.append(
            f"Verified repair tests are ready for {', '.join(repaired)}. Show the user each "
            "test and, only after they approve, call reqguard_apply_repair with confirm=true."
        )
    if counts.get("INSUFFICIENTLY_PROTECTED"):
        next_steps.append(
            "Some gaps have no verified repair. Report them as open risks; "
            "ReqGuard will not apply an unverified test."
        )
    if counts.get("INCONCLUSIVE"):
        next_steps.append("INCONCLUSIVE is not a pass. Report each reason to the user.")

    return {
        "report_dir": str(report_dir),
        "summary_html": str(report_dir / "summary.html"),
        "counts": counts,
        "requirements": items,
        "next_steps": next_steps,
    }


# ── background runs ───────────────────────────────────────────────────────────

@dataclass
class _Run:
    id: str
    repo: Path
    report_dir: Path
    log_path: Path
    dry_run: bool
    proc: subprocess.Popen
    started: float = field(default_factory=time.time)
    findings_mtime_before: float | None = None


_RUNS: dict[str, _Run] = {}
_RUNS_LOCK = threading.Lock()


def _active_run_for(repo: Path) -> _Run | None:
    with _RUNS_LOCK:
        for run in _RUNS.values():
            if run.repo == repo and run.proc.poll() is None:
                return run
    return None


def _run_status(run: _Run) -> dict:
    code = run.proc.poll()
    try:
        log = run.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log = ""
    out: dict[str, Any] = {
        "run_id": run.id,
        "repo": str(run.repo),
        "elapsed_seconds": round(time.time() - run.started, 1),
        "log_file": str(run.log_path),
    }
    if code is None:
        out["state"] = "running"
        out["output_tail"] = _tail(log, 15)
        out["hint"] = "Call reqguard_status with this run_id to keep waiting."
        return out

    out["state"] = "succeeded" if code == 0 else "failed"
    out["exit_code"] = code
    out["output_tail"] = _tail(log, 40)
    if code == 0 and not run.dry_run:
        findings = run.report_dir / "findings.json"
        fresh = findings.is_file() and (
            run.findings_mtime_before is None or findings.stat().st_mtime > run.findings_mtime_before
        )
        if fresh:
            out["result"] = summarize_findings(run.report_dir)
        else:
            out["warning"] = "The run exited 0 but did not write a new findings.json."
    elif code != 0:
        out["hint"] = (
            "The analysis failed. The output tail usually names the cause, such as a "
            "missing provider key, a red test suite, or a bad requirements path. "
            "reqguard_check_setup diagnoses the common ones."
        )
    return out


def _wait(run: _Run, seconds: float) -> None:
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline and run.proc.poll() is None:
        time.sleep(0.5)


# ── tools ─────────────────────────────────────────────────────────────────────

def tool_check_setup(args: dict[str, Any]) -> dict:
    repo = _repo(args)
    run_suite = bool(args.get("run_suite", True))
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, fix: str | None = None) -> None:
        entry = {"check": name, "ok": ok, "detail": detail}
        if fix and not ok:
            entry["fix"] = fix
        checks.append(entry)

    is_go = (repo / "go.mod").is_file()
    is_js_only = (repo / "package.json").is_file() and not any(repo.glob("**/*.py"))
    add(
        "python_project",
        not (is_go or is_js_only),
        "Python project" if not (is_go or is_js_only) else "not a Python project",
        "ReqGuard v1 supports Python/pytest projects only.",
    )

    cfg = _config(repo)
    has_yaml = (repo / "reqguard.yaml").is_file()
    add("config", True, "reqguard.yaml found" if has_yaml else "no reqguard.yaml, so defaults are used (fine)")

    req_path = (repo / cfg.requirements_path).resolve()
    req_ok = req_path.is_file() and req_path.read_text(encoding="utf-8", errors="replace").strip() != ""
    add(
        "requirements_file",
        req_ok,
        f"{req_path}" if req_ok else f"missing or empty: {req_path}",
        "Call reqguard_init to scaffold requirements.md, then write one testable rule "
        "per line, e.g. 'REQ-01: A cancelled order cannot be shipped.'",
    )

    set_keys = sorted({k for k in PROVIDER_KEY_NAMES if os.environ.get(k)} | (_dotenv_keys(repo) & set(PROVIDER_KEY_NAMES)))
    add(
        "provider_key",
        bool(set_keys),
        f"set: {', '.join(set_keys)}" if set_keys else "no LLM provider key found",
        "Set OPENROUTER_API_KEY (or CONDUIT_API_KEY) in your shell or in the repo's .env "
        "file, then restart the IDE's MCP server. Don't paste keys into chat.",
    )

    if run_suite:
        from reqguard.models import RunStatus
        from reqguard.sandbox import sandbox
        from reqguard.tools.test_runner import run_tests_with_config

        with sandbox(repo, cfg) as sb:
            outcome = run_tests_with_config(Path(sb), cfg)
        green = outcome.status is RunStatus.OK and outcome.exit_code == 0
        detail = (
            f"{outcome.passed} passed, {outcome.failed} failed, {outcome.errors} errors "
            f"(status {outcome.status.value}, exit {outcome.exit_code})"
        )
        if not green:
            detail += f"\n{_tail(outcome.stdout_tail or '', 15)}"
        fix = (
            "ReqGuard needs a green suite before it mutates anything. "
            f"Tests run with {sys.executable}. If imports fail, install ReqGuard into the "
            "project's own environment (pip install reqguard) or set testing.command in "
            "reqguard.yaml. Exit 4 with 'unrecognized arguments: --json-report' means "
            "pytest-json-report is missing: pip install pytest-json-report."
        )
        add("test_suite_green", green, detail, fix)

    ready = all(c["ok"] for c in checks)
    return {
        "repo": str(repo),
        "ready": ready,
        "interpreter": sys.executable,
        "checks": checks,
        "next_step": (
            "Ready. Call reqguard_analyze, starting with one requirement or dry_run=true."
            if ready
            else "Fix the failing checks above, then re-run reqguard_check_setup."
        ),
    }


_YAML_TEMPLATE = """\
# ReqGuard configuration. Every field is optional; these are the defaults.
requirements:
  path: requirements.md
testing:
  command: pytest          # e.g. "python -m pytest -q" or your venv's interpreter
  timeout_seconds: 300
analysis:
  max_repair_attempts: 2
  max_diff_lines: 15
report:
  output_dir: ./reqguard-report
"""

_REQUIREMENTS_TEMPLATE = """\
# Requirements

<!--
One testable rule per id. Say what must hold and what must be rejected.
ReqGuard will try to violate each rule and check whether your tests notice.
Replace the examples below with your project's real requirements.
-->

REQ-01: A cancelled order cannot be shipped. Attempting to ship a cancelled
order must be rejected.

REQ-02: A user cannot read another user's invoice.
"""


def tool_init(args: dict[str, Any]) -> dict:
    repo = _repo(args)
    created: list[str] = []
    skipped: list[str] = []
    for name, body in (("reqguard.yaml", _YAML_TEMPLATE), ("requirements.md", _REQUIREMENTS_TEMPLATE)):
        target = repo / name
        if target.exists():
            skipped.append(f"{name} (already exists, left untouched)")
            continue
        target.write_text(body, encoding="utf-8")
        created.append(name)
    return {
        "repo": str(repo),
        "created": created,
        "skipped": skipped,
        "next_steps": [
            "Replace the example rules in requirements.md with the project's real "
            "requirements, and confirm them with the user.",
            "Consider adding reqguard-report/ to .gitignore.",
            "Then call reqguard_check_setup.",
        ],
    }


def tool_analyze(args: dict[str, Any]) -> dict:
    repo = _repo(args)
    cfg = _config(repo)
    report_dir = _report_dir(repo, cfg)

    existing = _active_run_for(repo)
    if existing is not None:
        status = _run_status(existing)
        status["note"] = "An analysis is already running for this repo. Returning its status."
        return status

    argv = ["analyze", str(repo)]
    if args.get("requirement"):
        argv += ["--requirement", str(args["requirement"])]
    if args.get("requirements_path"):
        argv += ["--requirements", str(args["requirements_path"])]
    dry_run = bool(args.get("dry_run", False))
    if dry_run:
        argv.append("--dry-run")

    report_dir.mkdir(parents=True, exist_ok=True)
    findings = report_dir / "findings.json"
    run_id = uuid.uuid4().hex[:12]
    log_path = report_dir / f"mcp-run-{run_id}.log"
    log_fh = log_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            _cli(*argv),
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=_child_env(repo),
        )
    finally:
        log_fh.close()  # the child holds its own handle

    run = _Run(
        id=run_id,
        repo=repo,
        report_dir=report_dir,
        log_path=log_path,
        dry_run=dry_run,
        proc=proc,
        findings_mtime_before=findings.stat().st_mtime if findings.is_file() else None,
    )
    with _RUNS_LOCK:
        _RUNS[run_id] = run
    _log(f"started run {run_id}: {' '.join(argv)}")

    wait_seconds = float(args.get("wait_seconds", 45))
    _wait(run, min(wait_seconds, 600.0))
    return _run_status(run)


def tool_status(args: dict[str, Any]) -> dict:
    run_id = args.get("run_id")
    with _RUNS_LOCK:
        run = _RUNS.get(str(run_id)) if run_id else None
    if run is None:
        raise ToolError(f"unknown run_id {run_id!r}. Runs don't survive an MCP server restart.")
    _wait(run, min(float(args.get("wait_seconds", 30)), 600.0))
    return _run_status(run)


def tool_findings(args: dict[str, Any]) -> dict:
    repo = _repo(args)
    cfg = _config(repo)
    requirement = args.get("requirement")
    return summarize_findings(_report_dir(repo, cfg), requirement=requirement, detail=bool(requirement))


def tool_apply_repair(args: dict[str, Any]) -> dict:
    repo = _repo(args)
    requirement = args.get("requirement")
    if not requirement:
        raise ToolError("`requirement` is required, e.g. 'REQ-01'.")
    if args.get("confirm") is not True:
        raise ToolError(
            "Not applied. This writes a new test file into the user's repository. "
            "Show the user the test (reqguard_findings with this requirement), get "
            "their explicit approval, then call again with confirm=true."
        )
    cfg = _config(repo)
    argv = [
        "apply", str(repo),
        "--requirement", str(requirement),
        "--report-dir", str(_report_dir(repo, cfg)),
        "--yes",
    ]
    if args.get("prove", True):
        argv.append("--prove")
    try:
        proc = subprocess.run(
            _cli(*argv), cwd=repo, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=900, env=_child_env(repo),
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("reqguard apply timed out after 900s") from exc
    output = _tail((proc.stdout or "") + (proc.stderr or ""), 40)
    if proc.returncode != 0:
        raise ToolError(f"reqguard apply refused or failed (exit {proc.returncode}):\n{output}")
    return {"applied": True, "requirement": requirement, "exit_code": 0, "output": output}


# ── tool registry ─────────────────────────────────────────────────────────────

_REPO_PROP = {"type": "string", "description": "Absolute path to the Python project root."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "reqguard_check_setup",
        "title": "Check ReqGuard setup",
        "description": (
            "Check whether a project is ready for ReqGuard: Python project, requirements "
            "file, green pytest suite (run in a sandbox copy), and an LLM provider key "
            "(reports names only, never values). No model calls. Call this first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": _REPO_PROP,
                "run_suite": {"type": "boolean", "default": True, "description": "Run the test suite in a sandbox copy."},
            },
            "required": ["repo"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "handler": tool_check_setup,
    },
    {
        "name": "reqguard_init",
        "title": "Scaffold ReqGuard files",
        "description": (
            "Create reqguard.yaml and a requirements.md template in the project if they "
            "don't exist. Never overwrites existing files. No model calls."
        ),
        "inputSchema": {"type": "object", "properties": {"repo": _REPO_PROP}, "required": ["repo"]},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
        "handler": tool_init,
    },
    {
        "name": "reqguard_analyze",
        "title": "Analyze requirement protection",
        "description": (
            "Run ReqGuard. For each requirement: find the enforcing code, apply a "
            "requirement-violating mutation in a sandbox copy, run the real test suite, "
            "and, if the mutation survives, generate and verify a regression test. "
            "Returns PROTECTED / REPAIRED / INSUFFICIENTLY_PROTECTED / INCONCLUSIVE per "
            "requirement. Uses the LLM (costs tokens). Writes only to reqguard-report/. "
            "If the result says state=running, poll reqguard_status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": _REPO_PROP,
                "requirement": {"type": "string", "description": "Analyze one requirement id only, e.g. 'REQ-01'. Faster and cheaper."},
                "requirements_path": {"type": "string", "description": "Requirements file, relative to repo (overrides reqguard.yaml)."},
                "dry_run": {"type": "boolean", "default": False, "description": "Plan mutations only; don't execute or repair."},
                "wait_seconds": {"type": "number", "default": 45, "description": "How long to wait before returning a run_id (max 600)."},
            },
            "required": ["repo"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
        "handler": tool_analyze,
    },
    {
        "name": "reqguard_status",
        "title": "ReqGuard run status",
        "description": "Poll a background reqguard_analyze run. Returns the final findings when it finishes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "wait_seconds": {"type": "number", "default": 30, "description": "Wait up to this long for completion (max 600)."},
            },
            "required": ["run_id"],
        },
        "annotations": {"readOnlyHint": True},
        "handler": tool_status,
    },
    {
        "name": "reqguard_findings",
        "title": "Read ReqGuard findings",
        "description": (
            "Read the latest ReqGuard report for a project. With `requirement`, returns "
            "full detail: the mutation diff and the proposed regression test source. "
            "No model calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": _REPO_PROP,
                "requirement": {"type": "string", "description": "Requirement id for full detail."},
            },
            "required": ["repo"],
        },
        "annotations": {"readOnlyHint": True},
        "handler": tool_findings,
    },
    {
        "name": "reqguard_apply_repair",
        "title": "Apply a verified repair test",
        "description": (
            "Write ONE verified regression test from the report into the user's "
            "repository. Refuses unverified repairs and never overwrites a file. "
            "REQUIRES the user's explicit approval first: show them the test, then pass "
            "confirm=true. With prove=true (default), runs the repo suite afterwards and "
            "rolls the write back if it's not green."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": _REPO_PROP,
                "requirement": {"type": "string", "description": "Requirement id with status REPAIRED."},
                "confirm": {"type": "boolean", "description": "Must be true, set only after the user approves."},
                "prove": {"type": "boolean", "default": True},
            },
            "required": ["repo", "requirement", "confirm"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
        "handler": tool_apply_repair,
    },
]

_HANDLERS: dict[str, Callable[[dict[str, Any]], dict]] = {t["name"]: t["handler"] for t in TOOLS}


def _tool_listing() -> list[dict]:
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────

def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(name, str):
        return {"content": [{"type": "text", "text": "missing or invalid 'name'"}], "isError": True}
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
    try:
        payload = handler(args)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "structuredContent": payload, "isError": False}
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # never let a tool crash the server
        _log(traceback.format_exc())
        return {"content": [{"type": "text", "text": f"internal error: {exc!r}"}], "isError": True}


def handle_message(msg: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns a response, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg
    params = msg.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "ReqGuard checks whether a Python project's tests actually protect its "
                "written requirements. Call reqguard_check_setup first. Never call "
                "reqguard_apply_repair without the user's explicit approval."
            ),
        })
    if is_notification:
        return None  # notifications/initialized, notifications/cancelled, ...
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": _tool_listing()})
    if method == "tools/call":
        return _result(msg_id, _call_tool(params))
    return _error(msg_id, -32601, f"method not found: {method}")


def _send(stdout, lock: threading.Lock, message: dict) -> None:
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    with lock:
        stdout.write(data)
        stdout.flush()


def _serve_one(line: bytes, stdout, lock: threading.Lock) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        _send(stdout, lock, _error(None, -32700, "parse error"))
        return
    if not isinstance(msg, dict):
        _send(stdout, lock, _error(None, -32600, "invalid request"))
        return
    response = handle_message(msg)
    if response is not None:
        _send(stdout, lock, response)


def _take_protocol_streams():
    """Move the MCP pipes onto private fds and point fds 0/1 at devnull/stderr.

    Every child process (pytest, `reqguard analyze`) would otherwise inherit
    the protocol pipes. On Windows that deadlocks: the main thread sits in a
    blocking read on the stdin pipe, and creating a child that inherits the
    same pipe blocks behind it (synchronous I/O on one pipe object is
    serialized). A child writing to an inherited stdout would also corrupt the
    JSON-RPC stream. `os.dup` returns non-inheritable fds, so children never
    see the real pipes. On Windows, `dup2` onto 0/1 also updates the process's
    standard handles.
    """
    sys.stdout.flush()
    proto_in = os.dup(0)
    proto_out = os.dup(1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    os.dup2(2, 1)  # stray child/stdout writes land on stderr
    sys.stdout = sys.stderr
    return os.fdopen(proto_in, "rb"), os.fdopen(proto_out, "wb")


def main() -> int:
    """Serve MCP over stdio until the client closes stdin."""
    stdin, stdout = _take_protocol_streams()
    lock = threading.Lock()
    _log(f"ready (python {sys.executable})")
    for line in stdin:
        if not line.strip():
            continue
        # Each request gets its own thread so a slow tool (a waiting analyze)
        # doesn't block ping/status calls from the client.
        threading.Thread(target=_serve_one, args=(line, stdout, lock), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
