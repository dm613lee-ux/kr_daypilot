@echo off
cd /d "%~dp0"
if exist "output\fundamentals\latest.html" (
  start "" "output\fundamentals\latest.html"
) else (
  echo No fundamentals collection report found yet.
  pause
)
