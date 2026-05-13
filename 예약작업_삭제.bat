@echo off
setlocal

call "%~dp0uninstall_daily_task.bat"
exit /b %ERRORLEVEL%
