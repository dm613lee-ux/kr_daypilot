@echo off
cd /d "%~dp0"
if exist "output\value_momentum_mvp\latest.html" (
  start "" "output\value_momentum_mvp\latest.html"
) else (
  echo No Value Momentum MVP report found yet.
  pause
)
