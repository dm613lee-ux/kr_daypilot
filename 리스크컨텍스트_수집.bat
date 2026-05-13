@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.collect_risk_context --max-tickers 5

echo.
echo Risk context data is saved under data\live_context and output\risk_context.
pause
