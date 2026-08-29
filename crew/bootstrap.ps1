<#
    ReqGuard crew bootstrap - run ONCE, from the repo root.

        powershell -ExecutionPolicy Bypass -File crew\bootstrap.ps1

    Creates the git repo (if absent), the frozen-contract commit, three track
    branches, and three sibling worktrees. After this, each coding agent opens
    its own worktree and they cannot collide on the filesystem.

    ASCII only, and Windows PowerShell 5.1 compatible: no '&&', no ternary,
    no '??'. PS 5.1 reads a BOM-less .ps1 as ANSI, so non-ASCII characters
    would arrive mangled - keep this file plain.
#>

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Write-Host ""
Write-Host "ReqGuard crew bootstrap" -ForegroundColor Cyan
Write-Host "repo: $repo"
Write-Host ""

# --- 1. git repo ---------------------------------------------------------
if (-not (Test-Path (Join-Path $repo ".git"))) {
    Write-Host "[1/5] git init" -ForegroundColor Yellow
    git init -b main | Out-Null
} else {
    Write-Host "[1/5] git repo already present" -ForegroundColor DarkGray
}

# --- 2. .gitignore -------------------------------------------------------
$gitignore = Join-Path $repo ".gitignore"
if (-not (Test-Path $gitignore)) {
    Write-Host "[2/5] writing .gitignore" -ForegroundColor Yellow
    $ignore = @(
        "__pycache__/", "*.py[cod]", ".venv/", "venv/",
        ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
        "*.egg-info/", "dist/", "build/",
        "reqguard-report/", "benchmarks/results/", ".env"
    )
    Set-Content -Path $gitignore -Value $ignore -Encoding utf8
} else {
    Write-Host "[2/5] .gitignore present" -ForegroundColor DarkGray
}

# --- 3. the frozen-contract commit ---------------------------------------
# Everything parallel depends on this commit existing and not moving.
$hasCommit = $true
git rev-parse --verify HEAD *> $null
if ($LASTEXITCODE -ne 0) { $hasCommit = $false }

if (-not $hasCommit) {
    Write-Host "[3/5] creating the frozen-contract commit" -ForegroundColor Yellow
    git add -A
    $msg = "S0.1: freeze data contracts + crew protocol (Wave 0)"
    git commit -q -m $msg
} else {
    Write-Host "[3/5] HEAD exists - skipping initial commit" -ForegroundColor DarkGray
}

# --- 4. track branches ---------------------------------------------------
Write-Host "[4/5] track branches" -ForegroundColor Yellow
$tracks = @(
    @{ Role = "A"; Branch = "track/a-core";     Dir = "reqguard-A"; Desc = "hermes      - mutation + inspection" },
    @{ Role = "D"; Branch = "track/d-exec";     Dir = "reqguard-D"; Desc = "opencode    - execution + verification" },
    @{ Role = "B"; Branch = "track/b-agent";    Dir = "reqguard-B"; Desc = "claude_code - agent stages" },
    @{ Role = "E"; Branch = "track/e-bench";    Dir = "reqguard-E"; Desc = "antigravity - benchmark corpus" },
    @{ Role = "C"; Branch = "track/c-evidence"; Dir = "reqguard-C"; Desc = "kiro_cli    - evidence infrastructure" }
)

foreach ($t in $tracks) {
    git show-ref --verify --quiet ("refs/heads/" + $t.Branch)
    if ($LASTEXITCODE -ne 0) {
        git branch $t.Branch main
        Write-Host ("      created " + $t.Branch) -ForegroundColor Green
    } else {
        Write-Host ("      exists  " + $t.Branch) -ForegroundColor DarkGray
    }
}

# --- 5. worktrees --------------------------------------------------------
Write-Host "[5/5] worktrees" -ForegroundColor Yellow
$parent = Split-Path -Parent $repo

foreach ($t in $tracks) {
    $path = Join-Path $parent $t.Dir
    if (Test-Path $path) {
        Write-Host ("      exists  " + $path) -ForegroundColor DarkGray
    } else {
        git worktree add $path $t.Branch | Out-Null
        Write-Host ("      created " + $path) -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Ready." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Supervisor (here):" -ForegroundColor White
Write-Host "    python crew/crewctl.py dispatch"
Write-Host ""
foreach ($t in $tracks) {
    Write-Host ("  Track " + $t.Role + "  " + $t.Desc) -ForegroundColor White
    Write-Host ("    cd " + (Join-Path $parent $t.Dir))
    Write-Host ("    brief: crew/briefs/" + $t.Role + "-*.md   prompt: crew/prompts/")
    Write-Host ""
}
Write-Host "Wave 1 tracks run in parallel. Nothing they own overlaps." -ForegroundColor DarkGray
Write-Host ""
