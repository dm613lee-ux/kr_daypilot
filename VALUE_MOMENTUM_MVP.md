# Value/Quality + Momentum MVP

## 왜 이 전략인가

첨부 보고서를 다시 기준점으로 삼으면 우선순위는 다음과 같습니다.

PDF `국내 주식 투자 기법 검증 및 자동화 전략 연구.pdf`의 프로그램 개발 후보 Top 5:

1. 가치/퀄리티 + 모멘텀 멀티팩터 포트폴리오 봇
2. DART 공시 기반 기업가치 변화 탐지기
3. 종가베팅 수급 추적기
4. 과매도 패자 주식 평균 회귀 알리미
5. 이동평균선/돌파 매매 시뮬레이터

Markdown `deep-research-report.md`의 Top 5:

1. 다요인 스마트베타 추천엔진
2. 가치+모멘텀 스윙
3. 저변동성+추세 필터
4. 공시 이벤트 알림 엔진
5. 외인·기관 수급 보조전략

두 문서가 공통으로 지지하는 첫 구현 대상은 `가치/퀄리티 + 모멘텀`입니다. 따라서 이 MVP는 기존 Research Gate 2의 넓은 다요인 엔진이 아니라, 보고서의 1순위 후보를 좁게 구현합니다.

## 구현 범위

팩터:

- 가치: 낮은 PER, 낮은 PBR
- 퀄리티: 높은 ROE
- 모멘텀: 6개월 및 12개월 시장 대비 상대 모멘텀

필터:

- KOSPI/KOSDAQ만 사용
- 최소 시가총액 1,000억 원
- 정책 파일의 20일 평균 거래대금 기준 적용
- PER/PBR이 0 이하이면 차단
- ROE 또는 6개월 모멘텀 결측이면 차단
- 공시 리스크 플래그가 있으면 기본 차단

검증:

- 기본 월간 리밸런싱
- 신호일 다음 거래일 시가 진입, 다음 리밸런싱 종료일 종가 청산
- 왕복 비용과 슬리피지 차감
- 동일 유동성/시총 조건의 평균 수익률을 벤치마크로 사용

안전:

- 실전 주문 없음
- 최대 승격 상태는 `paper_only`
- 성과가 좋아도 주문 자동화는 `disabled`

## 실행

원클릭:

```text
가치모멘텀MVP_실행.bat
가치모멘텀MVP_결과_열기.bat
```

CLI:

```powershell
python -m kr_precision_backtest.run_value_momentum_mvp
```

유용한 옵션:

```powershell
python -m kr_precision_backtest.run_value_momentum_mvp --frequency weekly
python -m kr_precision_backtest.run_value_momentum_mvp --portfolio-size 10 --max-periods 24
python -m kr_precision_backtest.run_value_momentum_mvp --slippage-pct 0.5
```

## 출력

최신 결과:

```text
output/value_momentum_mvp/latest.html
output/value_momentum_mvp/latest_summary.json
output/value_momentum_mvp/latest_recommendations.csv
output/value_momentum_mvp/latest_periods.csv
output/value_momentum_mvp/latest_trades.csv
```

실험 기록:

```text
experiments/EXP_YYYYMMDD_HHMMSS_VALUE_MOMENTUM_MVP/
experiments/registry_value_momentum_mvp.csv
```

## 다음 판단 기준

이 MVP가 `paper_only`로 나오면 바로 실전 주문으로 가지 않습니다. 다음 순서가 필요합니다.

1. 주간 리밸런싱과 포트폴리오 크기 변경에서 성과가 유지되는지 확인
2. 슬리피지를 높여도 초과수익이 남는지 확인
3. 최신 추천 후보를 사람이 검토하고, 공시 이벤트 차단이 지나치게 넓거나 좁은지 확인
4. 최소 몇 주 동안 paper-only 추천을 누적 기록
