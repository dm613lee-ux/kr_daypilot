@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

python -m venv .venv
if errorlevel 1 (
  echo Failed to create the Python virtual environment.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .

echo.
echo KR DayPilot environment installation is complete.
echo The package is installed in editable mode, so src imports work without PYTHONPATH.
pause
