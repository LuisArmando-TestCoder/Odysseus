#Requires -Version 5.1
<#
  Odysseus - Windows installer (the downloadable "executable").

  Easiest: double-click install-windows.cmd (it runs this script).
  Or from PowerShell:
    irm https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-windows.ps1 | iex
  Or, downloaded:
    powershell -ExecutionPolicy Bypass -File .\install-windows.ps1

  It:
    1. ensures git (winget Git.Git if missing)
    2. ensures Python 3.11+ (winget Python.Python.3.12 if missing)
    3. clones Odysseus into %USERPROFILE%\odysseus (override: $env:ODYSSEUS_HOME)
    4. runs the installer: credentials, environment, hourly auto-update from
       the main branch, shortcuts, optional start-at-login, first launch

  Safe to re-run: an existing install is updated and repaired in place.
#>
$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:ODYSSEUS_REPO_URL) { $env:ODYSSEUS_REPO_URL } else { "https://github.com/LuisArmando-TestCoder/Odysseus.git" }
$InstallDir = if ($env:ODYSSEUS_HOME) { $env:ODYSSEUS_HOME } else { Join-Path $HOME "odysseus" }

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Step "Odysseus - Windows installer"
Write-Host "  install folder: $InstallDir"

# -- 1. git ------------------------------------------------------------------
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Step "git not found - installing with winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget is not available. Install Git for Windows from https://git-scm.com/download/win and re-run."
    }
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { Fail "git install did not complete. Open a new terminal and re-run." }
}
Write-Host "  OK git: $($git.Source)"

# -- 2. Python 3.11+ -----------------------------------------------------------
function Get-PyVersion($launcher, $launcherArgs) {
    try { return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim() } catch { return $null }
}
function Find-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in @("-3.13", "-3.12", "-3.11")) {
            if (Get-PyVersion $py.Source @($v)) { return @{ exe = $py.Source; args = @($v) } }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    # Skip the Microsoft Store alias (WindowsApps\python.exe), which only opens the Store.
    if ($python -and ($python.Source -notmatch "WindowsApps")) {
        $ver = Get-PyVersion $python.Source @()
        if ($ver) {
            $parts = $ver.Split('.')
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                return @{ exe = $python.Source; args = @() }
            }
        }
    }
    return $null
}

$pyInfo = Find-Python
if (-not $pyInfo) {
    Write-Step "Python 3.11+ not found - installing with winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget is not available. Install Python 3.11+ from https://www.python.org/downloads/ and re-run."
    }
    winget install --id Python.Python.3.12 -e --source winget --scope user --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $pyInfo = Find-Python
    if (-not $pyInfo) { Fail "Python install did not complete. Open a new terminal and re-run." }
}
Write-Host "  OK Python: $($pyInfo.exe) $($pyInfo.args -join ' ')"

# -- 3. Clone (or reuse) -------------------------------------------------------
if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Step "Odysseus is already installed here - it will be updated and repaired"
} elseif ((Test-Path $InstallDir) -and (Get-ChildItem -Force $InstallDir | Select-Object -First 1)) {
    Fail "$InstallDir exists and isn't an Odysseus install. Move it, or set `$env:ODYSSEUS_HOME to another folder."
} else {
    Write-Step "Cloning $RepoUrl"
    & git clone $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Fail "git clone failed." }
}

# -- 4. Installer ----------------------------------------------------------------
Write-Step "Running the Odysseus installer"
Set-Location $InstallDir
$pyArgs = @($pyInfo.args) + @("installer\install.py") + @($args)
& $pyInfo.exe @pyArgs
if ($LASTEXITCODE -ne 0) { Fail "The installer reported an error (see above)." }
Write-Host ""
Read-Host "Done. Press Enter to close this window"
