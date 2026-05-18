@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\logs" mkdir "runtime\logs"
set "LOG=%CD%\runtime\logs\fundamentals.log"
echo ===== KR DayPilot fundamentals collection start %DATE% %TIME% ===== > "%LOG%"
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -m kr_precision_backtest.collect_fundamentals >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
type "%LOG%"
if "%CODE%"=="0" (
  if exist "output\fundamentals\latest.html" start "" "output\fundamentals\latest.html"
) else (
  echo Fundamentals collection failed. See "%LOG%".
)
pause
exit /b %CODE%
