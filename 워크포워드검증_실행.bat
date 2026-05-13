@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.run_walk_forward_validation

echo.
echo Walk-forward validation results are saved under output\historical_intraday_walk_forward.
pause
