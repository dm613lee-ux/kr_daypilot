@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\risk_context\latest.html" (
  start "" "output\risk_context\latest.html"
) else (
  echo No risk context report exists yet. Run 리스크컨텍스트_수집.bat first.
  pause
)
