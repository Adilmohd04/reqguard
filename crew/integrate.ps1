<#
    ReqGuard integration gate - Supervisor only, run from the repo root.

        powershell -ExecutionPolicy Bypass -File crew\integrate.ps1 -Track A
        powershell -ExecutionPolicy Bypass -File crew\integrate.ps1 -Track A -Merge

    Order of checks. Any failure stops before merge:
      1. ownership   the branch touched only paths it owns
      2. imports     no anthropic in A or C; no reqguard.agent in A or C
      3. gates       every gate command for that track's done-candidate tasks
      4. merge       only with -Merge, and only if 1-3 are clean

    Green in the agent's worktree is not evidence. This runs here.

    Windows PowerShell 5.1 compatible: no '&&', no ternary, no '??'.
#>

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("A", "B", "C", "D", "E")]
    [string]$Track,

    [string[]]$Tasks,          # e.g. -Tasks A1,A2,A3 ; omit to infer from the board
    [switch]$Merge
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$branchMap = @{ "A" = "track/a-core"; "B" = "track/b-agent"; "C" = "track/c-evidence";
                "D" = "track/d-exec";  "E" = "track/e-bench" }
$branch = $branchMap[$Track]

Write-Host ""
Write-Host "INTEGRATION GATE - Track $Track ($branch)" -ForegroundColor Cyan
Write-Host ("=" * 60)

# -- 1 + 2. ownership and import rules -------------------------------------
Write-Host ""
Write-Host "[1] ownership + import rules" -ForegroundColor Yellow
python crew/crewctl.py check $Track
if ($LASTEXITCODE -ne 0) {
    Write-Host "REJECTED at ownership/import check. Nothing merged." -ForegroundColor Red
    Write-Host "Tell the agent exactly which path or import was wrong." -ForegroundColor DarkGray
    exit 1
}

# -- 3. gates --------------------------------------------------------------
Write-Host "[2] task gates" -ForegroundColor Yellow

if (-not $Tasks) {
    Write-Host "      no -Tasks given; pass them explicitly, e.g. -Tasks A1,A2,A3" -ForegroundColor Yellow
    exit 2
}

# Gates must run against the branch's code, so check it out first.
$startBranch = (git rev-parse --abbrev-ref HEAD).Trim()
git checkout -q $branch

$failed = @()
foreach ($t in $Tasks) {
    python crew/crewctl.py gate $t
    if ($LASTEXITCODE -ne 0) { $failed += $t }
}

git checkout -q $startBranch

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host ("REJECTED - failing gates: " + ($failed -join ", ")) -ForegroundColor Red
    Write-Host "Nothing merged. The task is not done." -ForegroundColor DarkGray
    exit 1
}

Write-Host ""
Write-Host "ALL CHECKS PASSED" -ForegroundColor Green

# -- 4. merge --------------------------------------------------------------
if ($Merge) {
    Write-Host ""
    Write-Host "[3] merging $branch into main" -ForegroundColor Yellow
    git checkout -q main
    git merge --no-ff $branch -m "integrate: track $Track ($($Tasks -join ', '))"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "MERGE CONFLICT. This should be impossible under the ownership map." -ForegroundColor Red
        Write-Host "A conflict means two tracks wrote one file - fix ownership.yaml," -ForegroundColor DarkGray
        Write-Host "do not just resolve the conflict and move on." -ForegroundColor DarkGray
        exit 1
    }
    Write-Host "Merged. Now set status: done in crew/tasks.yaml for: $($Tasks -join ', ')" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Dry run. Re-run with -Merge to merge into main." -ForegroundColor DarkGray
}
Write-Host ""
