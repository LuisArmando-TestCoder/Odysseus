@echo off
rem Odysseus - Windows entry point for shortcuts and autostart.
rem Starts installer\launch.py without a console window, using the venv's
rem pythonw.exe when it works, otherwise the Python launcher (pyw), so a
rem broken venv can still be repaired by launch.py. Arguments pass through.
setlocal
set "REPO=%~dp0.."
pushd "%REPO%" >nul

set "PYW="
if exist "%REPO%\venv\Scripts\pythonw.exe" (
  "%REPO%\venv\Scripts\python.exe" -c "pass" >nul 2>nul && set "PYW=%REPO%\venv\Scripts\pythonw.exe"
)
if not defined PYW (
  where pyw >nul 2>nul && set "PYW=pyw"
)
if not defined PYW (
  where pythonw >nul 2>nul && set "PYW=pythonw"
)
if not defined PYW (
  echo Odysseus needs Python 3.11+. Re-run the installer from https://github.com/LuisArmando-TestCoder/Odysseus
  pause
  exit /b 1
)

if /I "%PYW%"=="pyw" (
  start "" /b pyw -3 "%REPO%\installer\launch.py" --background %*
) else (
  start "" /b "%PYW%" "%REPO%\installer\launch.py" --background %*
)
popd >nul
endlocal
