@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo KR DayPilot 웹앱을 시작합니다.
python -m kr_precision_backtest.launch_web_app
if errorlevel 1 (
  echo.
  echo KR DayPilot 웹앱 실행 또는 상태 점검에 실패했습니다.
  pause
  exit /b 1
)
echo.
echo 브라우저가 열리지 않으면 위 주소를 직접 열어주세요.
pause
