@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.collect_intraday --max-tickers 2

echo.
echo 遺꾨큺 ?곗씠?곕뒗 data\intraday\minute_bars ?대뜑????λ맗?덈떎.
pause

