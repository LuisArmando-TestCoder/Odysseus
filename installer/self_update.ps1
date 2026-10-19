# Odysseus — daily self-update (Windows).
#
# Safe, non-destructive:
#   * fetch + fast-forward only (never rewrites local commits)
#   * stash dirty tree first, pop after
#   * reinstall dependencies ONLY when requirements.txt changed
#   * never touches .env or data/ (both gitignored)
#
# Invoked by the installer-registered Scheduled Task. Can run manually:
#   powershell -ExecutionPolicy Bypass -File installer\self_update.ps1
$ErrorActionPreference = "Stop"

# Repo root = parent of this script's directory.
$repoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoDir "logs"
$logFile = Join-Path $logDir "self-update.log"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

function Log {
    param([string]$msg)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logFile -Value $line
}

Log "== self-update start =="

try {
    Set-Location $repoDir
} catch {
    Log "ERROR: cannot cd $repoDir"
    exit 1
}

# 1. Fetch all remotes, prune deleted branches.
& git fetch --all --prune *>> $logFile
if ($LASTEXITCODE -ne 0) {
    Log "ERROR: git fetch failed"
    exit 1
}

# Capture requirements.txt before pull so we can detect changes.
$reqBefore = $null
if (Test-Path "requirements.txt") {
    $reqBefore = (& git rev-parse HEAD:requirements.txt 2>$null)
    if ($LASTEXITCODE -ne 0) { $reqBefore = Get-Content -Raw "requirements.txt" }
}

# 2. Stash local changes (if any) so fast-forward can proceed.
$stashed = $false
$dirty = (& git status --porcelain)
if ($dirty) {
    & git stash push -m "odysseus-auto-update $(Get-Date -Format yyyyMMddHHmmss)" *>> $logFile
    if ($LASTEXITCODE -eq 0) {
        $stashed = $true
        Log "stashed local changes before pull"
    } else {
        Log "WARN: git stash failed — attempting pull anyway"
    }
}

# 3. Fast-forward only.
$pullOk = $false
& git pull --ff-only *>> $logFile
if ($LASTEXITCODE -eq 0) {
    $pullOk = $true
    Log "pull: up to date / fast-forwarded"
} else {
    Log "WARN: git pull --ff-only failed (diverged history). Manual reconciliation needed."
}

# 4. Restore local changes.
if ($stashed) {
    & git stash pop *>> $logFile
    if ($LASTEXITCODE -eq 0) { Log "restored stashed changes" }
    else { Log "WARN: stash pop failed (see git stash list)" }
}

# 5. Reinstall dependencies if requirements.txt changed.
if ($pullOk -and (Test-Path "requirements.txt")) {
    $reqAfter = (& git rev-parse HEAD:requirements.txt 2>$null)
    if ($LASTEXITCODE -ne 0) { $reqAfter = Get-Content -Raw "requirements.txt" }
    if ($reqBefore -ne $reqAfter) {
        Log "requirements.txt changed — reinstalling"
        $pip = Join-Path $repoDir "venv\Scripts\pip.exe"
        if (Test-Path $pip) {
            & $pip install -r requirements.txt *>> $logFile
            if ($LASTEXITCODE -eq 0) { Log "dependencies reinstalled" }
            else { Log "WARN: pip install failed" }
        } else {
            Log "WARN: venv not found — skipping dependency reinstall (run the installer)"
        }
    } else {
        Log "requirements.txt unchanged — skipping reinstall"
    }
}

# 6. Idempotent first-run/setup pass.
$python = Join-Path $repoDir "venv\Scripts\python.exe"
if (Test-Path $python) {
    $env:ODYSSEUS_SKIP_RUN_HINT = "1"
    & $python (Join-Path $repoDir "setup.py") *>> $logFile
    if ($LASTEXITCODE -eq 0) { Log "setup.py pass ok" }
    else { Log "WARN: setup.py pass failed" }
}

# 7. Acceptance: report current HEAD.
$head = (& git rev-parse --short HEAD 2>$null)
if ($head) { Log "git HEAD: $head" } else { Log "git HEAD: ?" }
Log "== self-update end =="
exit 0