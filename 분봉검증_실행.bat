@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.run_intraday_backtest

echo.
echo 遺꾨큺 寃利?寃곌낵??output\intraday ?대뜑???앹꽦?⑸땲??
pause


