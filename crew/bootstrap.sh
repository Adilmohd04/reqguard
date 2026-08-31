#!/usr/bin/env bash
# ReqGuard crew bootstrap - SHARED-TREE mode. Run ONCE, by the supervisor.
#
#     bash crew/bootstrap.sh
#
# All five agents share one working directory, so this does NOT create branches
# or worktrees. In a shared tree a `git checkout` by one agent rewrites files
# under every other agent mid-edit -- so there is one branch, `main`, and file
# ownership (crew/ownership.yaml) is the collision guard.

set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"

echo
echo "ReqGuard crew bootstrap (shared-tree mode)"
echo "repo: $REPO"
echo

# --- 1. git repo ----------------------------------------------------------
if [ ! -d .git ]; then
  echo "[1/4] git init"
  git init -b main >/dev/null
else
  echo "[1/4] git repo already present"
fi

# --- 2. .gitignore --------------------------------------------------------
if [ ! -f .gitignore ]; then
  echo "[2/4] writing .gitignore"
  cat > .gitignore <<'EOF'
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
dist/
build/
reqguard-report/
benchmarks/results/
.env
EOF
else
  echo "[2/4] .gitignore present"
fi

# --- 3. python env --------------------------------------------------------
if [ ! -d .venv ]; then
  echo "[3/4] creating .venv"
  uv venv --python 3.12 >/dev/null 2>&1 || python3 -m venv .venv
fi
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=.venv/Scripts/python.exe; fi
"$PY" -m pip install -q pyyaml pydantic 2>/dev/null || uv pip install -q pyyaml pydantic
echo "[3/4] python env ready: $PY"

# --- 4. the frozen-contract commit ---------------------------------------
# Every track depends on models.py + errors.py and on nothing else shared.
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "[4/4] creating the frozen-contract commit"
  git add -A
  git commit -q -m "S0.1: freeze data contracts + crew protocol (Wave 0)"
else
  echo "[4/4] HEAD exists - skipping initial commit"
fi

echo
echo "Ready. One tree, one branch (main), five agents."
echo
echo "  Supervisor:"
echo "    $PY crew/crewctl.py dispatch"
echo
echo "  Every agent, before EVERY commit:"
echo "    $PY crew/crewctl.py guard <ROLE>     # A B C D E"
echo "    $PY crew/crewctl.py stage <ROLE>     # git add, only your files"
echo
echo "  Roles:  A hermes | B claude_code | D opencode_cli"
echo "          E antigravity_cli | C+S kiro_cli (supervisor)"
echo
