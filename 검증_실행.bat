@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.run_backtest --max-reference-days 250

echo.
echo 寃곌낵 ?뚯씪? output ?대뜑???앹꽦?⑸땲??
pause

