@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\logs" mkdir "runtime\logs"
set "LOG=%CD%\runtime\logs\research_gate2.log"
echo ===== KR DayPilot Research Gate 2 start %DATE% %TIME% ===== > "%LOG%"
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -m kr_precision_backtest.run_research_gate2 >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
type "%LOG%"
if "%CODE%"=="0" (
  if exist "output\research_gate2\latest.html" start "" "output\research_gate2\latest.html"
) else (
  echo Research Gate 2 failed. See "%LOG%".
)
pause
exit /b %CODE%
