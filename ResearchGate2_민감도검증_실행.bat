@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\logs" mkdir "runtime\logs"
set "LOG=%CD%\runtime\logs\research_gate2_sensitivity.log"
echo ===== KR DayPilot RG2 sensitivity start %DATE% %TIME% ===== > "%LOG%"
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -m kr_precision_backtest.run_rg2_sensitivity >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
type "%LOG%"
if "%CODE%"=="0" (
  if exist "output\research_gate2_sensitivity\latest.html" start "" "output\research_gate2_sensitivity\latest.html"
) else (
  echo RG2 sensitivity validation failed. See "%LOG%".
)
pause
exit /b %CODE%
