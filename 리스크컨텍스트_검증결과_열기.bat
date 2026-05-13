@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\risk_context_validation\latest.html" (
  start "" "output\risk_context_validation\latest.html"
) else (
  echo No risk context validation report exists yet. Run 리스크컨텍스트_검증.bat first.
  pause
)
