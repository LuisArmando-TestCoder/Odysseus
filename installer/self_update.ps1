# Odysseus - scheduled self-update (Windows).
#
# The Scheduled Task registered by the installer runs this every hour. All the
# logic lives in installer\updater.py (stdlib only); this wrapper only finds a
# Python 3 to run it with, so updates keep working even if the venv is broken.
# See docs\auto-update.md.
#
#   powershell -ExecutionPolicy Bypass -File installer\self_update.ps1
#   powershell -ExecutionPolicy Bypass -File installer\self_update.ps1 --check
$ErrorActionPreference = "Continue"

$repoDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $repoDir "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

$candidates = @(
    @{ exe = (Join-Path $repoDir "venv\Scripts\python.exe"); args = @() },
    @{ exe = "py"; args = @("-3") },
    @{ exe = "python"; args = @() }
)
$py = $null
foreach ($c in $candidates) {
    $cmd = Get-Command $c.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    & $cmd.Source @($c.args) -c "import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] >= 8 else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $c; $py.exe = $cmd.Source; break }
}
if (-not $py) {
    Add-Content -Path (Join-Path $logDir "self-update.log") -Value ("[{0}] ERROR: no python found for the updater" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    exit 1
}

$env:ODYSSEUS_UPDATE_QUIET = "1"
& $py.exe @($py.args) (Join-Path $repoDir "installer\updater.py") --scheduled @args
exit $LASTEXITCODE
