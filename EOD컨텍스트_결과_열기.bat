@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\eod_context\latest.html" (
  start "" "output\eod_context\latest.html"
) else (
  echo No EOD context report found yet.
  echo Run EOD컨텍스트_수집.bat first.
  pause
)
