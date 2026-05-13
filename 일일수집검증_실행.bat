@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -m kr_precision_backtest.daily_pipeline --max-tickers 2

echo.
echo ?쇱씪 ?섏쭛쨌寃利?寃곌낵??output\daily_pipeline ?대뜑???앹꽦?⑸땲??
echo ?꾩쟻 ?깃낵 ??쒕낫?쒕뒗 output\dashboard\latest.html ???앹꽦?⑸땲??
pause

