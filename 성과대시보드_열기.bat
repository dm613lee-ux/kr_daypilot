@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\dashboard\latest.html" (
  start "" "output\dashboard\latest.html"
) else (
  echo 아직 누적 성과 대시보드가 없습니다. 먼저 일일수집검증_실행.bat 을 실행하세요.
  pause
)

