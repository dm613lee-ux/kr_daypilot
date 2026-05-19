@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist "output\investment_recommender\latest.html" (
  echo 아직 결과 파일이 없습니다. 먼저 투자근거추천_실행.bat 을 실행하세요.
  pause
  exit /b 1
)
start "" "output\investment_recommender\latest.html"
