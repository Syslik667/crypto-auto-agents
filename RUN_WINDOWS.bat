@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 agent.py
) else (
  python agent.py
)
if exist "reports\LATEST.html" start "" "reports\LATEST.html"
pause
