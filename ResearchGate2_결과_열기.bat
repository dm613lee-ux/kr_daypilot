@echo off
cd /d "%~dp0"
if exist "output\research_gate2\latest.html" (
  start "" "output\research_gate2\latest.html"
) else (
  echo No Research Gate 2 report found yet.
  pause
)
