@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.backfill_historical_intraday --max-candidates 50 --sleep-seconds 0.7

echo.
echo Historical intraday backfill for up to 50 candidates is complete.
echo Then run the historical intraday simulation launcher to refresh the report.
pause

