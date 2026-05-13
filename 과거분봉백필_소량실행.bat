@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.backfill_historical_intraday --max-candidates 5 --sleep-seconds 1

echo.
echo Historical intraday backfill results are saved under data\historical_intraday.
pause

