@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\intraday\latest.html" (
  start "" "output\intraday\latest.html"
) else (
  echo 아직 분봉 검증 결과가 없습니다. 먼저 분봉검증_실행.bat 을 실행하세요.
  pause
)
