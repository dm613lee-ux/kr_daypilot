@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo This can call KIS hundreds or thousands of times depending on candidate count.
echo It is resumable, but it may take a long time and may hit API rate limits.
choice /C YN /M "Continue full historical intraday backfill"
if errorlevel 2 exit /b 1

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.backfill_historical_intraday --max-candidates 500 --sleep-seconds 0.9

echo.
echo Full historical intraday backfill attempt is complete.
echo Then run the historical intraday simulation launcher to refresh the report.
pause

