# Open Source Integration Status

기준 문서: `OPEN_SOURCE_DUE_DILIGENCE.md`  
목표: 외부 오픈소스/공식 API를 무작정 붙이지 않고, 국내 주식 MVP에 실제 도움이 되는 것만 안전하게 반영한다.

## 현재 반영 상태

| 자산 | 실사 결론 | 현재 반영 | 다음 조치 |
|---|---|---|---|
| `pykrx` | 핵심 국내 데이터 소스 | `collect_eod_context.py`, `collect_fundamentals.py`, `diagnose_krx_access.py`, `collect_price_history.py`에서 optional adapter로 사용 | 현재 실행 환경에는 미설치. 환경 설치 뒤 가격/수급/밸류에이션 갱신에 사용 |
| `FinanceDataReader` | 가격/유니버스 보강 핵심 | `pyproject.toml` 의존성과 `collect_price_history.py` optional FDR 가격 어댑터에 반영 | 현재 셸에는 미설치. `환경_설치.bat` 또는 editable install 후 stale price 해소용 1차 소스로 사용 |
| OpenDART 공식 API | 공시/재무 핵심 | `collect_fundamentals.py`, `collect_eod_context.py`, `collect_risk_context.py`에서 직접 HTTP API 사용 | `OpenDartReader` 래퍼는 아직 미도입. 현재 직접 API 방식 유지 |
| `OpenDartReader` | OpenDART 편의 래퍼 | 아직 코드 의존성 없음 | 직접 API에서 부족한 공시 목록/기업코드 탐색 보조로 후보 |
| `dart-fss` | XBRL/재무제표 보강 | 아직 코드 의존성 없음 | 재무 계정 커버리지 부족이 확인될 때 보조 adapter로 추가 |
| KIS 공식 API | 실시간 시세/호가/paper fill 보조 | 내부 `kis_client.py`, 분봉/리스크 컨텍스트 수집에 반영 | 실전 주문은 계속 금지. 호가/체결 기반 paper realism만 확장 |
| `python-kis` | KIS wrapper 후보 | 아직 코드 의존성 없음 | 내부 client와 기능 비교 후 필요 endpoint만 참고 또는 대체 검토 |
| `quantstats` | 성과 리포트 | 아직 코드 의존성 없음 | 추천기 이후 paper 성과 로그가 누적된 뒤 리포트 보강 |
| `PyPortfolioOpt` / `Riskfolio-Lib` | 포트폴리오 sizing 보조 | 아직 코드 의존성 없음 | 종목 추천 안정화 뒤 risk cap/sector cap 실험용 |
| `bt` / `alphalens` | 백테스트/팩터 검증 보조 | 아직 코드 의존성 없음 | in-house point-in-time 검증 유지 후 교차검증용 |
| `OpenBB`, `vectorbt`, `backtesting.py`, `backtrader` | 라이선스/구조 리스크 | 코어 의존성에서 제외 | 참고만 |
| AI Agent/MCP 계열 | 설명/조회 보조 | 아직 추천권한 없음 | read-only analyst mode 후보. 점수 산출/주문 판단 권한 금지 |

## 이번 반영 내용

`OPEN_SOURCE_DUE_DILIGENCE.md`의 핵심 결론 중 `pykrx + FinanceDataReader`를 가격 데이터 갱신 경로에 반영했다.

추가된 실행 경로:

```text
python -m kr_precision_backtest.run_recommender_pipeline --price-source auto --price-max-tickers 200 --eod-max-tickers 30 --fundamental-max-tickers 30 --top 15
python -m kr_precision_backtest.collect_price_history --source auto --max-tickers 300
가격데이터갱신_실행.bat
투자근거추천_실행.bat
```

동작 방식:

- `투자근거추천_실행.bat`은 갱신 파이프라인을 먼저 실행한 뒤 추천기를 실행한다.
- 파이프라인 순서는 가격 갱신 -> EOD 수급/공시 갱신 -> 펀더멘털/밸류에이션 갱신 -> 투자근거 추천이다.
- 가격 갱신은 `pykrx-bulk`를 먼저 시도하고, 환경/세션 문제로 비어 있으면 FDR per-ticker fallback을 사용한다.
- FDR이 없거나 종목별 조회가 비어 있으면 `pykrx` 종목별 가격/시총 조회를 마지막 fallback으로 시도한다.
- 외부 패키지가 설치되어 있지 않으면 추천 엔진을 깨지 않고 `missing_dependency`로 보고한다.
- 새 행은 `data/kr_stock_price_history.csv` 스키마로 정규화한 뒤 기존 가격 파일과 `(ticker, source_bas_dt)` 기준으로 병합한다.
- 실사에서 경고한 stale/source/schema 리스크 때문에 데이터 신선도 차단은 추천기에서 계속 유지한다.

## 원칙

- 외부 프로젝트의 스타 수는 관심도 지표일 뿐 신뢰성 근거로 쓰지 않는다.
- 라이선스가 불명확하거나 AGPL/GPL/Commons Clause 리스크가 있는 프로젝트는 코어 의존성에 넣지 않는다.
- AI/MCP는 설명과 조회 보조로 제한하고, 추천 점수와 주문 의도 생성은 결정론적 코드가 담당한다.
- 실전 주문 자동화는 MVP 범위에서 계속 제외한다.
