@echo off
cd /d "%~dp0"
echo Starting KR DayPilot web app...
python -m kr_precision_backtest.launch_web_app
if errorlevel 1 (
  echo.
  echo KR DayPilot launcher failed.
  pause
  exit /b 1
)
echo.
echo If the browser did not open, use the URL shown above.
pause
