@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -B -m local_telegram.bot_setup
    exit /b 0
)
echo Run TELEGRAM_LOGIN.bat first.
pause
exit /b 1
