@echo off
rem Builds "Ankiji.exe" and a zip to share: dist\Ankiji.zip
rem Also used by GitHub Actions (CI=true there: plain python, never waits for a key press).
setlocal
set "PAUSE=pause"
if defined CI set "PAUSE=ver >nul"
cd /d "%~dp0"
title Building Ankiji

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if defined CI set "PY=python"
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || (%PAUSE% & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller==6.22.3 || (echo Install failed. & %PAUSE% & exit /b 1)

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
  --name "Ankiji" --icon "%cd%\assets\icon.ico" ^
  --add-data "%cd%\app\static;app\static" ^
  --collect-submodules uvicorn --hidden-import app.server ^
  --workpath build --distpath dist --specpath build ^
  run.py || (echo Build failed. & %PAUSE% & exit /b 1)

rem The build must never contain your own settings, key or cache.
del /q "dist\Ankiji\settings.json" "dist\Ankiji\nadeshiko_cache.json" "dist\Ankiji\app-log.txt" 2>nul
rmdir /s /q "dist\Ankiji\browser-profile" 2>nul
copy /y "README.md" "dist\Ankiji\README.md" >nul
copy /y "LICENSE" "dist\Ankiji\LICENSE.txt" >nul
copy /y "THIRD_PARTY_NOTICES.md" "dist\Ankiji\THIRD_PARTY_NOTICES.md" >nul

if exist "dist\Ankiji.zip" del "dist\Ankiji.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Ankiji' -DestinationPath 'dist\Ankiji.zip'" || (echo Zip failed. & %PAUSE% & exit /b 1)

echo.
echo Done. Send this file:  %cd%\dist\Ankiji.zip
%PAUSE%
