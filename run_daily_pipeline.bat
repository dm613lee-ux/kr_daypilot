@echo off
setlocal
cd /d "%~dp0"

if not exist "runtime\logs" mkdir "runtime\logs"
set "LOG=%~dp0runtime\logs\daily_pipeline_task.log"

echo.>>"%LOG%"
echo ===== KR DayPilot daily pipeline start %DATE% %TIME% =====>>"%LOG%"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.daily_pipeline --max-tickers 2 >>"%LOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"

echo ===== KR DayPilot daily pipeline exit %EXITCODE% %DATE% %TIME% =====>>"%LOG%"
exit /b %EXITCODE%


