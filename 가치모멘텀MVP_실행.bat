@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\logs" mkdir "runtime\logs"
set "LOG=%CD%\runtime\logs\value_momentum_mvp.log"
echo ===== KR DayPilot Value Momentum MVP start %DATE% %TIME% ===== > "%LOG%"
set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -m kr_precision_backtest.run_value_momentum_mvp >> "%LOG%" 2>&1
set "CODE=%ERRORLEVEL%"
type "%LOG%"
if "%CODE%"=="0" (
  if exist "output\value_momentum_mvp\latest.html" start "" "output\value_momentum_mvp\latest.html"
) else (
  echo Value Momentum MVP failed. See "%LOG%".
)
pause
exit /b %CODE%
