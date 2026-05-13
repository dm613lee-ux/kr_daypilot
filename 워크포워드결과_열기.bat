@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\historical_intraday_walk_forward\latest.html" (
  start "" "output\historical_intraday_walk_forward\latest.html"
) else (
  echo No walk-forward validation report found yet.
  echo Run 워크포워드검증_실행.bat first.
  pause
)
