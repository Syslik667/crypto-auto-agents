@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if /i "%GITHUB_ACTIONS%"=="true" exit /b 1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B -m game_rewards.server --open
) else (
    where py >nul 2>nul
    if errorlevel 1 (
        python -B -m game_rewards.server --open
    ) else (
        py -3 -B -m game_rewards.server --open
    )
)
if errorlevel 1 (
    echo Check Python 3.11 or newer and whether port 8766 is already in use.
    pause
    exit /b 1
)
