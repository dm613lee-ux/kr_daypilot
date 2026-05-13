@echo off
setlocal
cd /d "%~dp0"

call "%~dp0install_daily_task.bat"
exit /b %ERRORLEVEL%
