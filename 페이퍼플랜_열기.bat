@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\app\latest.html" (
  start "" "output\app\latest.html"
) else (
  echo No paper order plan found yet.
  echo Run 페이퍼플랜_실행.bat first.
  pause
)
