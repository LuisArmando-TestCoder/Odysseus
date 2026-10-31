@echo off
rem Odysseus - Windows installer. Double-click this file.
rem
rem It runs the latest install-windows.ps1 from the main branch with a
rem one-time execution-policy bypass (nothing on the system is changed).
rem If Windows SmartScreen asks, choose "More info" -> "Run anyway".
title Odysseus installer
setlocal
set "PS1_URL=https://raw.githubusercontent.com/LuisArmando-TestCoder/Odysseus/main/installer/bootstrap/install-windows.ps1"

rem Prefer a copy sitting next to this file (offline / pinned use).
if exist "%~dp0install-windows.ps1" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $s = Invoke-RestMethod '%PS1_URL%'; & ([scriptblock]::Create($s)) %*"
)
if errorlevel 1 (
  echo.
  echo The installer did not finish. Scroll up for the error.
  pause
)
endlocal
