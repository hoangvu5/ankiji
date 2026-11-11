@echo off
setlocal
cd /d "%~dp0"
title Ankiji

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>nul || (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup. Then run this file again.
  pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting things up, this takes a minute...
  %PY% -m venv .venv || (pause & exit /b 1)
)
if not exist ".venv\installed.flag" (
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || (echo Install failed. & pause & exit /b 1)
  echo ok> ".venv\installed.flag"
)

echo.
echo Ankiji is running at http://127.0.0.1:8770
echo The app opens in its own window. Close that window to stop the app.
echo.
".venv\Scripts\python.exe" run.py
pause
