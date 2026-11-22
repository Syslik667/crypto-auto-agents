@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if /i "%GITHUB_ACTIONS%"=="true" exit /b 1
if exist ".venv\Scripts\python.exe" goto deps
where py >nul 2>nul
if errorlevel 1 (
    python -m venv ".venv"
) else (
    py -3 -m venv ".venv"
)
if errorlevel 1 goto fail
:deps
".venv\Scripts\python.exe" -c "import sys, tkinter, win32crypt, telethon; sys.exit(not (sys.version_info >= (3,11) and telethon.__version__ == '1.46.0'))" >nul 2>nul
if not errorlevel 1 goto launch
".venv\Scripts\python.exe" -m pip install -r "local_telegram\requirements.txt"
if errorlevel 1 goto fail
:launch
".venv\Scripts\python.exe" -c "import sys, tkinter; sys.exit(sys.version_info < (3,11))" >nul 2>nul
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -B -m local_telegram.login_gui
if errorlevel 1 goto fail
exit /b 0
:fail
echo Startup failed. Install Python 3.11+ for Windows with Tcl/Tk and check your internet connection.
pause
exit /b 1
