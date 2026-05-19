@echo off
chcp 65001 > nul
cd /d "%~dp0"
python -m kr_precision_backtest.run_recommender_pipeline
if errorlevel 1 (
  echo.
  echo 데이터 갱신 또는 투자근거 추천 실행 중 오류가 발생했습니다.
  pause
  exit /b 1
)
echo.
echo 완료: output\investment_recommender\latest.html
pause
