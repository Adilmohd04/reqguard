"""Install ReqGuard into an IDE agent as an MCP server plus a skill.

    reqguard install kiro   [--project PATH | --user]
    reqguard install codex  [--project PATH | --user]

What gets written (merged, never clobbered):

| IDE   | MCP server config                         | Skill                                   |
|-------|-------------------------------------------|-----------------------------------------|
| kiro  | <project>/.kiro/settings/mcp.json         | <project>/.kiro/skills/reqguard/SKILL.md |
| codex | <project>/.codex/config.toml              | <project>/.agents/skills/reqguard/SKILL.md |

With ``--user`` the same files go under the home directory (``~/.kiro/...``,
``~/.codex/config.toml``, ``~/.agents/skills/...``), so every project gets it.

The MCP server is launched with the *absolute path of the interpreter running
this command*. So run ``reqguard install`` from the environment that should run
your tests (usually the project's venv). ReqGuard runs pytest with that same
interpreter, so the project's dependencies must be importable from it.

No secrets are ever written. The provider key stays in the user's shell or in
the project's ``.env``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

__all__ = ["install", "InstallResult", "SUPPORTED_IDES"]

SUPPORTED_IDES = ("kiro", "codex")
SERVER_KEY = "reqguard"
MODULE = "reqguard.integrations.mcp_server"


@dataclass
class InstallResult:
    ide: str
    written: list[str]
    unchanged: list[str]
    notes: list[str]


def _skill_text() -> str:
    return resources.files("reqguard.integrations").joinpath("skill/SKILL.md").read_text(encoding="utf-8")


def _write_if_changed(path: Path, text: str, result: InstallResult) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        result.unchanged.append(str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    result.written.append(str(path))


def _kiro_mcp(path: Path, python: str, result: InstallResult) -> None:
    config: dict = {}
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                config = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} is not valid JSON; fix or remove it first ({exc})") from exc
        if not isinstance(config, dict):
            raise ValueError(f"{path} must contain a JSON object")
    servers = config.setdefault("mcpServers", {})
    entry = dict(servers.get(SERVER_KEY) or {})
    entry.update({"command": python, "args": ["-m", MODULE], "disabled": False})
    entry.setdefault("env", {})
    entry.setdefault("autoApprove", ["reqguard_check_setup", "reqguard_status", "reqguard_findings"])
    servers[SERVER_KEY] = entry
    _write_if_changed(path, json.dumps(config, indent=2) + "\n", result)


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string literal is a valid TOML basic string


def _codex_block(python: str) -> str:
    return (
        f"[mcp_servers.{SERVER_KEY}]\n"
        f"command = {_toml_str(python)}\n"
        f"args = [\"-m\", {_toml_str(MODULE)}]\n"
        "startup_timeout_sec = 30\n"
        "# reqguard_analyze returns within its own wait window and hands back a run_id,\n"
        "# but give slow calls (apply --prove runs the whole suite) room to finish.\n"
        "tool_timeout_sec = 900\n"
    )


def _codex_mcp(path: Path, python: str, result: InstallResult) -> None:
    block = _codex_block(python)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    header = f"[mcp_servers.{SERVER_KEY}]"
    if header in existing:
        # Replace only our own table: from its header up to the next table header.
        lines = existing.splitlines(keepends=True)
        out: list[str] = []
        skipping = False
        for line in lines:
            stripped = line.strip()
            if stripped == header:
                skipping = True
                out.append(block)
                continue
            if skipping and stripped.startswith("[") and not stripped.startswith(f"[mcp_servers.{SERVER_KEY}."):
                skipping = False
            if not skipping:
                out.append(line)
        new = "".join(out)
    else:
        sep = "" if not existing or existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        new = existing + sep + block
    _write_if_changed(path, new, result)


def install(ide: str, *, project: Path | None = None, user: bool = False, python: str | None = None) -> InstallResult:
    """Register the ReqGuard MCP server and skill for ``ide``. Idempotent."""
    if ide not in SUPPORTED_IDES:
        raise ValueError(f"unsupported IDE {ide!r}; choose one of {', '.join(SUPPORTED_IDES)}")
    python = python or sys.executable
    base = Path.home() if user else (project or Path.cwd()).resolve()
    if not base.is_dir():
        raise ValueError(f"not a directory: {base}")

    result = InstallResult(ide=ide, written=[], unchanged=[], notes=[])
    skill = _skill_text()

    if ide == "kiro":
        _kiro_mcp(base / ".kiro" / "settings" / "mcp.json", python, result)
        _write_if_changed(base / ".kiro" / "skills" / "reqguard" / "SKILL.md", skill, result)
        result.notes.append("In Kiro, reconnect MCP servers from the MCP Server panel (or restart Kiro).")
    else:
        _codex_mcp(base / ".codex" / "config.toml", python, result)
        _write_if_changed(base / ".agents" / "skills" / "reqguard" / "SKILL.md", skill, result)
        if not user:
            result.notes.append(
                "Codex only reads a project's .codex/config.toml when the project is trusted. "
                "Otherwise use `reqguard install codex --user`."
            )
        result.notes.append("Restart Codex so it picks up the MCP server and the $reqguard skill.")

    result.notes.append(
        "Set OPENROUTER_API_KEY (or CONDUIT_API_KEY) in your shell or the project's .env. "
        "Keys are never written by this installer."
    )
    result.notes.append(f"MCP server interpreter: {python}. Tests will run with it.")
    return result
