@echo off
rem Builds "Anki Card Maker.exe" and a zip to share: dist\AnkiCardMaker.zip
rem Also used by GitHub Actions (CI=true there: plain python, never waits for a key press).
setlocal
set "PAUSE=pause"
if defined CI set "PAUSE=ver >nul"
cd /d "%~dp0"
title Building Anki Card Maker

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if defined CI set "PY=python"
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || (%PAUSE% & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller==6.22.3 || (echo Install failed. & %PAUSE% & exit /b 1)

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
  --name "Anki Card Maker" --icon "%cd%\assets\icon.ico" ^
  --add-data "%cd%\app\static;app\static" ^
  --collect-submodules uvicorn --hidden-import app.server ^
  --workpath build --distpath dist --specpath build ^
  run.py || (echo Build failed. & %PAUSE% & exit /b 1)

rem The build must never contain your own settings, key or cache.
del /q "dist\Anki Card Maker\settings.json" "dist\Anki Card Maker\nadeshiko_cache.json" "dist\Anki Card Maker\app-log.txt" 2>nul
rmdir /s /q "dist\Anki Card Maker\browser-profile" 2>nul
copy /y "README.md" "dist\Anki Card Maker\README.md" >nul

if exist "dist\AnkiCardMaker.zip" del "dist\AnkiCardMaker.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Anki Card Maker' -DestinationPath 'dist\AnkiCardMaker.zip'" || (echo Zip failed. & %PAUSE% & exit /b 1)

echo.
echo Done. Send this file:  %cd%\dist\AnkiCardMaker.zip
%PAUSE%
