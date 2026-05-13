@echo off
setlocal

schtasks /Delete /TN "KR DayPilot Daily Collect" /F

echo.
echo Windows scheduled task deleted: KR DayPilot Daily Collect
pause

