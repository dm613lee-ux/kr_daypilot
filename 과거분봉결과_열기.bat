@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\historical_intraday\latest.html" (
  start "" "output\historical_intraday\latest.html"
) else (
  echo No historical intraday simulation result exists yet. Run the backfill and simulation launchers first.
  pause
)
