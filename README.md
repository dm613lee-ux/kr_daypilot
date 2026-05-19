# KR DayPilot

국내 주식 투자 후보를 선별하고, 사용자 판단과 paper portfolio 성과를 추적하는 로컬 웹앱입니다.

이 프로젝트의 현재 운영본은 실전 주문을 수행하지 않습니다. 추천 결과는 매수 확정 신호가 아니라 `paper_review` 기반의 투자 검토 자료입니다.

## 실행

파일 탐색기에서 아래 파일을 실행합니다.

```text
KR_DayPilot_웹앱_실행.bat
```

실행기는 서버를 시작하고 `/api/health` 상태 점검 후 브라우저를 엽니다. 기본 포트가 사용 중이면 다음 포트로 자동 회피합니다.

직접 실행:

```powershell
python -m kr_precision_backtest.launch_web_app
```

## 사용 흐름

1. 상단 상태가 `paper_review`이고 데이터가 `fresh`인지 확인합니다.
2. `추천` 목록에서 점수와 투자기법을 보고 후보를 선택합니다.
3. 오른쪽 상세 패널에서 근거, 점수 구성, 진입/목표/손절 계획을 확인합니다.
4. 후보별로 `관심 저장`, `제외`, `메모 저장`을 남깁니다.
5. 실제 매수 전에는 `Paper 추가`로 paper portfolio ledger에 먼저 기록합니다.
6. 이후 데이터 갱신 후 paper ledger에서 성과를 추적합니다.

## 현재 운영 구성

```text
webapp/                                  정적 웹 UI
src/kr_precision_backtest/run_web_app.py 서버 API
src/kr_precision_backtest/launch_web_app.py 실행기
src/kr_precision_backtest/run_recommender_pipeline.py 데이터 갱신 + 추천 파이프라인
src/kr_precision_backtest/investment_recommender.py 투자근거 추천 엔진
runtime/webapp/                          사용자 판단, 메모, paper ledger
data/kr_stock_price_history.csv          가격 히스토리
data/eod_context/                        수급/공시 컨텍스트
data/fundamentals/                       재무/밸류에이션 데이터
output/investment_recommender/           최신 추천 결과
```

## 주요 명령

```powershell
python -m kr_precision_backtest.run_recommender_pipeline --price-source auto --price-max-tickers 200 --eod-max-tickers 30 --fundamental-max-tickers 30 --top 15
python -m kr_precision_backtest.run_investment_recommender --top 15
python -m kr_precision_backtest.collect_price_history --source auto --max-tickers 200
```

## 정리 정책

과거 실험, 기각된 검증 앱, ResearchGate, 분봉/스윙/가치모멘텀 MVP 산출물은 운영본에서 제외했습니다. 삭제하지 않고 `_archive/` 아래로 이동해 필요하면 되돌릴 수 있게 보존합니다.

현재 루트에는 운영에 필요한 파일만 남기는 것을 원칙으로 합니다.
