@echo off
chcp 65001 > nul
cd /d "%~dp0"
python -m kr_precision_backtest.collect_price_history --source auto --max-tickers 300
if errorlevel 1 (
  echo.
  echo 가격 데이터 갱신 중 오류가 발생했습니다. FinanceDataReader 또는 pykrx 설치 상태와 네트워크를 확인하세요.
  pause
  exit /b 1
)
echo.
echo 완료: data\kr_stock_price_history.csv
pause
