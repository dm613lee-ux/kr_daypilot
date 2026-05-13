@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "output\feature_validation\latest.html" (
  start "" "output\feature_validation\latest.html"
) else (
  echo No feature stop analysis report found yet.
  echo Run 피처손절분석_실행.bat first.
  pause
)
