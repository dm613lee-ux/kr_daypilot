@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.run_historical_intraday_backtest

echo.
echo Historical intraday simulation results are saved under output\historical_intraday.
pause

