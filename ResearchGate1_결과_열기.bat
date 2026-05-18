@echo off
cd /d "%~dp0"
if exist "output\research_gate1\latest.html" (
  start "" "output\research_gate1\latest.html"
) else (
  echo No Research Gate 1 report found yet.
  pause
)
