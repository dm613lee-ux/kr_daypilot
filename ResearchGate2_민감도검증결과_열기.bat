@echo off
cd /d "%~dp0"
if exist "output\research_gate2_sensitivity\latest.html" (
  start "" "output\research_gate2_sensitivity\latest.html"
) else (
  echo No RG2 sensitivity report found yet.
  pause
)
