@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\daily_pipeline\latest.html" (
  start "" "output\daily_pipeline\latest.html"
) else (
  echo 아직 일일 파이프라인 결과가 없습니다. 먼저 일일수집검증_실행.bat 을 실행하세요.
  pause
)

