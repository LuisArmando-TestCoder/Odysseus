#Requires -Version 5.1
<#
  Odysseus — Windows bootstrap installer.

  Downloads and runs on a fresh Windows machine. It:
    1. ensures git is present (winget install Git.Git if missing)
    2. ensures Python 3.11+ is present (winget install Python.Python.3.11)
    3. clones the Odysseus repo
    4. runs the interactive credential wizard + environment setup

  Safe to re-run: every step is idempotent.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
#>
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/LuisArmando-TestCoder/Odysseus.git"
$InstallDir = Join-Path $HOME "odysseus"

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Step "Odysseus — Windows installer"

# ── 1. git ────────────────────────────────────────────────────────────────
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Step "git not found — installing via winget"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Fail "winget is not available. Install Git for Windows from https://git-scm.com/download/win and re-run."
    }
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { Fail "git install did not complete. Re-open a terminal and re-run." }
}
Write-Host "  OK git: $($git.Source)"

# ── 2. Python 3.11+ ───────────────────────────────────────────────────────
function Get-PyVersion($launcher, $args) {
    try { return (& $launcher @args -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim() } catch { return $null }
}

$pyExe = $null
$pyArgs = @()

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    foreach ($v in @("-3.13", "-3.12", "-3.11")) {
        $ver = Get-PyVersion $pyLauncher.Source @($v)
        if ($ver) { $pyExe = $pyLauncher.Source; $pyArgs = @($v); break }
    }
}

if (-not $pyExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $ver = Get-PyVersion $pythonCmd.Source @()
        if ($ver) {
            $parts = $ver.Split('.')
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                $pyExe = $pythonCmd.Source
            }
        }
    }
}

if (-not $pyExe) {
    Write-Step "Python 3.11+ not found — installing via winget"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Fail "winget is not available. Install Python 3.11+ from https://www.python.org/downloads/ and re-run."
    }
    winget install --id Python.Python.3.11 -e --source winget --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($v in @("-3.13", "-3.12", "-3.11")) {
            $ver = Get-PyVersion $pyLauncher.Source @($v)
            if ($ver) { $pyExe = $pyLauncher.Source; $pyArgs = @($v); break }
        }
    }
    if (-not $pyExe) { Fail "Python install did not complete. Re-open a terminal and re-run." }
}
Write-Host "  OK Python: $pyExe $($pyArgs -join ' ')"

# ── 3. Clone ──────────────────────────────────────────────────────────────
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Step "Repo already present — updating"
    & git -C $InstallDir pull --ff-only
} else {
    Write-Step "Cloning $RepoUrl"
    & git clone $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Fail "git clone failed." }
}

# ── 4. Run the installer wizard ───────────────────────────────────────────
Write-Step "Running the Odysseus installer (credentials + environment setup)"
Set-Location $InstallDir
& $pyExe @pyArgs installer/install.py