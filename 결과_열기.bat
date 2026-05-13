@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\latest.html" (
  start "" "output\latest.html"
) else (
  echo 아직 결과 파일이 없습니다. 먼저 검증_실행.bat 을 실행하세요.
  pause
)
