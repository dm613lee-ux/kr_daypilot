@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\krx_access\latest.html" (
  start "" "output\krx_access\latest.html"
) else (
  echo No KRX access diagnosis report found yet.
  echo Run KRX접근진단_실행.bat first.
  pause
)
