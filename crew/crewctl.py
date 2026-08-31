#!/usr/bin/env python3
"""crewctl — the Supervisor's tool.

Computes what is dispatchable, enforces file ownership and import rules on a
track branch, and runs task gates. No model calls; pure bookkeeping.

    python crew/crewctl.py board                 # full task board
    python crew/crewctl.py dispatch              # tasks whose deps are satisfied
    python crew/crewctl.py guard A               # what YOU may touch in the shared tree
    python crew/crewctl.py stage A               # git add, only your files
    python crew/crewctl.py check A               # branch-diff check (worktree mode only)
    python crew/crewctl.py gate A3               # run one task's gate

Exit codes: 0 ok, 1 violation, 2 usage error.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("crewctl needs pyyaml:  uv pip install pyyaml")

# Windows consoles default to cp1252; a non-ASCII char would crash a report.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
    pass

ROOT = Path(__file__).resolve().parent.parent
OWNERSHIP = ROOT / "crew" / "ownership.yaml"
TASKS = ROOT / "crew" / "tasks.yaml"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
)


# -- loading -──────────────────────────────────────────────────────────────

def load_ownership() -> dict:
    return yaml.safe_load(OWNERSHIP.read_text(encoding="utf-8"))


def load_tasks() -> list[dict]:
    data = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    # tasks.yaml is a list with two leading scalar keys; keep only mappings
    # that carry an id.
    if isinstance(data, dict):
        data = data.get("tasks", [])
    return [t for t in data if isinstance(t, dict) and "id" in t]


def owned_patterns(own: dict, role: str) -> list[str]:
    return own.get(f"owned_by_{role}", []) or []


def matches(path: str, patterns: list[str]) -> bool:
    path = path.replace("\\", "/")
    for pat in patterns:
        pat = pat.replace("\\", "/")
        if pat.endswith("/**"):
            if path.startswith(pat[:-3] + "/") or path == pat[:-3]:
                return True
        if fnmatch.fnmatch(path, pat):
            return True
    return False


# -- git helpers -──────────────────────────────────────────────────────────

def gate_env() -> dict[str, str]:
    """Environment for gate commands.

    Gates are written with bare `python` / `pytest` / `reqguard`. On Windows a
    bare `python` hits the Store stub, so put the interpreter running crewctl
    at the front of PATH -- every gate then resolves to the same environment
    the Supervisor is using, with no per-machine paths baked into tasks.yaml.

    `src` goes on PYTHONPATH so contract gates work before S0.3's editable
    install exists.
    """
    env = dict(os.environ)
    scripts = str(Path(sys.executable).parent)
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")
    return env


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True
    )
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{out.stderr.strip()}")
    return out.stdout


def changed_files(branch: str, base: str = "main") -> list[str]:
    raw = git("diff", "--name-only", f"{base}...{branch}")
    return [line.strip() for line in raw.splitlines() if line.strip()]


# -- commands -─────────────────────────────────────────────────────────────

def cmd_board() -> int:
    tasks = load_tasks()
    by_owner: dict[str, list[dict]] = {}
    for t in tasks:
        by_owner.setdefault(t.get("owner", "?"), []).append(t)

    mark = {"done": f"{GREEN}[x]{RESET}", "in_progress": f"{YELLOW}[~]{RESET}",
            "blocked": f"{RED}[!]{RESET}", "review": f"{YELLOW}[?]{RESET}"}
    for owner in sorted(by_owner):
        print(f"\n── {owner} " + "─" * 50)
        for t in by_owner[owner]:
            box = mark.get(t.get("status", "todo"), "[ ]")
            crit = f" {RED}*{RESET}" if t.get("critical") else ""
            needs = t.get("needs") or []
            dep = f"  {DIM}needs: {', '.join(needs)}{RESET}" if needs else ""
            print(f"  {box} {t['id']:<6} {t['title']}{crit}{dep}")
    print(f"\n{DIM}* = critical: a defect here invalidates the benchmark{RESET}")
    return 0


def cmd_dispatch() -> int:
    tasks = load_tasks()
    status = {t["id"]: t.get("status", "todo") for t in tasks}

    ready, waiting = [], []
    for t in tasks:
        if status[t["id"]] == "done":
            continue
        unmet = [n for n in (t.get("needs") or []) if status.get(n) != "done"]
        (waiting if unmet else ready).append((t, unmet))

    print(f"\n{GREEN}DISPATCHABLE NOW{RESET}")
    if not ready:
        print("  (nothing - every remaining task is blocked)")
    for t, _ in ready:
        crit = f" {RED}CRITICAL{RESET}" if t.get("critical") else ""
        print(f"  {t['owner']}  {t['id']:<6} {t['title']}{crit}")
        print(f"        {DIM}brief: crew/briefs/{t['owner']}-*.md"
              f"   gate: {t.get('gate', '(none)')}{RESET}")

    print(f"\n{DIM}BLOCKED{RESET}")
    for t, unmet in waiting:
        print(f"  {DIM}{t['owner']}  {t['id']:<6} {t['title']}"
              f"   waiting on: {', '.join(unmet)}{RESET}")
    print()
    return 0


def cmd_check(role: str) -> int:
    own = load_ownership()
    roles = own.get("roles", {})
    if role not in roles:
        print(f"unknown role {role!r}; expected one of {sorted(roles)}")
        return 2

    branch = roles[role]["branch"]
    mine = owned_patterns(own, role)
    files = changed_files(branch)

    print(f"\nChecking {branch} ({len(files)} changed files)\n")
    violations: list[str] = []

    # 1. ownership
    for f in files:
        if not matches(f, mine):
            owner = "?"
            for other in roles:
                if matches(f, owned_patterns(own, other)):
                    owner = other
                    break
            violations.append(
                f"OWNERSHIP  {f}\n"
                f"           owned by {owner}, not {role}. Revert it."
            )

    # 2. import rules
    deny_anthropic = re.compile(r"^\s*(import\s+anthropic|from\s+anthropic)", re.M)
    if role in ("A", "C", "D", "E"):
        for f in files:
            p = ROOT / f
            if p.suffix == ".py" and p.exists():
                if deny_anthropic.search(p.read_text(encoding="utf-8", errors="ignore")):
                    violations.append(
                        f"IMPORT     {f}\n"
                        f"           Track {role} may not import anthropic. "
                        f"Only Track B owns the SDK surface."
                    )
        deny_agent = re.compile(r"^\s*from\s+reqguard\.agent|^\s*import\s+reqguard\.agent", re.M)
        for f in files:
            p = ROOT / f
            if p.suffix == ".py" and p.exists():
                if deny_agent.search(p.read_text(encoding="utf-8", errors="ignore")):
                    violations.append(
                        f"IMPORT     {f}\n"
                        f"           Track {role} may not import reqguard.agent. "
                        f"This track must run with no model and no key."
                    )

    if violations:
        print(f"{RED}REJECTED — {len(violations)} violation(s){RESET}\n")
        for v in violations:
            print("  " + v.replace("\n", "\n  ") + "\n")
        return 1

    print(f"{GREEN}OK{RESET} — ownership and import rules clean\n")
    return 0


def cmd_gate(task_id: str) -> int:
    tasks = {t["id"]: t for t in load_tasks()}
    t = tasks.get(task_id)
    if t is None:
        print(f"no such task: {task_id}")
        return 2
    gate = t.get("gate")
    if not gate:
        print(f"{YELLOW}{task_id} has no gate - cannot be marked done{RESET}")
        return 1

    print(f"\n{task_id}: {t['title']}\ngate: {gate}\n" + "-" * 60)
    rc = subprocess.run(gate, shell=True, cwd=ROOT, env=gate_env()).returncode
    print("-" * 60)
    if rc == 0:
        print(f"{GREEN}GATE PASSED{RESET}  {task_id}\n")
    else:
        print(f"{RED}GATE FAILED{RESET}  {task_id} (exit {rc}) — not done\n")
    return rc


# -- shared-tree mode ------------------------------------------------------
# Every agent runs in ONE working directory, so there are no branch diffs to
# inspect and `git checkout` is destructive to everyone else. These two
# commands are the collision guard in that mode.

def working_tree_files() -> list[str]:
    """Modified + staged + untracked paths in the shared working tree.

    `--untracked-files=all` is mandatory here, not cosmetic: without it git
    collapses a wholly-untracked directory to a single `dir/` porcelain entry,
    so exact ownership patterns cannot match the owned files inside it. `guard`
    then reports them as ``owned by ?`` and `stage` silently omits them. Asking
    for every untracked file expands the directory to individual paths so
    ownership matching sees the real files.
    """
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT, capture_output=True, text=True,
    )
    files = []
    for line in out.stdout.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


def cmd_guard(role: str) -> int:
    """Show which dirty paths belong to `role` and which belong to others.

    Run before every commit. In a shared tree the danger is not a merge
    conflict -- it is staging another agent's half-finished work.
    """
    own = load_ownership()
    roles = own.get("roles", {})
    if role not in roles:
        print(f"unknown role {role!r}; expected one of {sorted(roles)}")
        return 2

    mine = owned_patterns(own, role)
    dirty = working_tree_files()
    if not dirty:
        print(f"{GREEN}clean{RESET} - nothing modified")
        return 0

    ours, theirs = [], []
    for f in dirty:
        if matches(f, mine):
            ours.append(f)
        else:
            owner = "?"
            for other in roles:
                if matches(f, owned_patterns(own, other)):
                    owner = other
                    break
            theirs.append((f, owner))

    print("")
    print(f"Working tree: {len(dirty)} dirty path(s)")
    print("")
    if ours:
        print(f"{GREEN}YOURS ({role}){RESET}")
        for f in ours:
            print(f"  {f}")
        print("")
    if theirs:
        print(f"{YELLOW}NOT YOURS - do not stage these{RESET}")
        for f, owner in theirs:
            print(f"  {f}   (owned by {owner})")
        print("")
        print(f"{DIM}Another agent is mid-task on those. Staging them would")
        print(f"commit their half-finished work.{RESET}")
        print("")

    print(f"{DIM}Stage only your own:  python3 crew/crewctl.py stage {role}{RESET}")
    print("")
    return 1 if theirs else 0


def cmd_stage(role: str) -> int:
    """`git add` ONLY this role's dirty files.

    `git add -A` in a shared tree stages every other agent's work in progress.
    This is the safe equivalent, and the only staging command an agent should
    ever run.
    """
    own = load_ownership()
    if role not in own.get("roles", {}):
        print(f"unknown role {role!r}")
        return 2
    mine = [f for f in working_tree_files()
            if matches(f, owned_patterns(own, role))]
    if not mine:
        print("nothing of yours is dirty")
        return 0
    for f in mine:
        print(f"  + {f}")
    rc = subprocess.run(["git", "add", *mine], cwd=ROOT).returncode
    if rc == 0:
        print("")
        print(f"{GREEN}staged {len(mine)} file(s){RESET}")
        print(f'{DIM}now:  git commit -m "<TASK-ID>: <what changed>"{RESET}')
    return rc


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, *rest = argv[1:]
    if cmd == "board":
        return cmd_board()
    if cmd == "dispatch":
        return cmd_dispatch()
    if cmd == "guard":
        return cmd_guard(rest[0].upper()) if rest else 2
    if cmd == "stage":
        return cmd_stage(rest[0].upper()) if rest else 2
    if cmd == "check":
        return cmd_check(rest[0].upper()) if rest else 2
    if cmd == "gate":
        return cmd_gate(rest[0]) if rest else 2
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
