@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\swing_backtest\latest.html" (
  start "" "output\swing_backtest\latest.html"
) else (
  echo No swing backtest report found yet.
  echo Run 스윙검증_실행.bat first.
  pause
)
