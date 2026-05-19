# KR DayPilot Open Source Due Diligence

조사일: 2026-05-18 KST  
범위: 국내 주식 추천/매매보조 MVP에 활용 가능한 GitHub, 오픈소스, AI Skill, MCP, 퀀트 라이브러리 생태계  
원칙: Star 수는 관심도 지표일 뿐이며, 신뢰성 근거로 쓰지 않는다. 최종 기준은 국내 주식 MVP에 실제로 쓸 수 있는지이다.

## I. Executive Summary

현재 KR DayPilot의 방향은 `DART Event Impact Gate` 단독 추천 엔진이 아니라, `Alpha Ensemble Recommendation Gate`이다. 핵심 알파는 다요인 펀더멘털 랭킹, 가치+모멘텀, 저변동성+추세, 공시 이벤트 필터, 외국인/기관 수급 보조로 구성하는 편이 맞다.

실사 결론은 다음과 같다.

1. 데이터 수집은 `pykrx`, `FinanceDataReader`, `OpenDartReader`, `dart-fss`가 가장 현실적이다. 단, KRX/네이버/공시 API 구조 변경에 취약하므로 소스별 freshness, schema, 누락 검사를 필수로 둔다.
2. KIS는 국내 MVP의 실시간 시세, 호가, paper fill 현실성 검증에 가장 적합하다. 공식 `koreainvestment/open-trading-api`는 최신성이 좋지만 명시 오픈소스 라이선스가 확인되지 않아 코드 복사는 피하고 API 사용법과 샘플 참조 중심으로 쓴다.
3. 실전 주문 자동화는 금지한다. 오픈소스 자동매매 프로젝트 대부분은 주문 안정성, 예외 처리, 리스크 게이트, 라이선스, 최신 API 적합성이 부족하다. KR DayPilot은 `paper_only`와 매매보조 화면까지만 열어야 한다.
4. 백테스트 엔진은 당장 대형 프레임워크를 도입하기보다 현재의 pandas 기반 point-in-time 검증기를 유지하고, `quantstats`, `bt`, `PyPortfolioOpt`, `Riskfolio-Lib`를 보조 도구로 붙이는 조합이 가장 실용적이다.
5. AI Agent/MCP는 추천 엔진이 아니라 분석 보조 인터페이스로만 써야 한다. `korea-stock-mcp`, `pykrx-mcp`, KIS MCP, TradingAgents류는 데이터 조회, 설명, 보고서 초안에는 쓸 수 있지만 매수/매도 판단 권한을 주면 안 된다.
6. 라이선스 위험이 크다. `OpenBB`는 AGPL, `backtesting.py`는 AGPL, `vectorbt`와 `PyBroker`는 Commons Clause 계열, `ai-hedge-fund`와 KIS 공식 샘플 일부는 GitHub API 기준 명시 라이선스가 없었다. MVP 의존성에는 permissive 라이선스 중심으로 제한한다.

추천 기본 스택:

```text
pykrx + FinanceDataReader + OpenDartReader/dart-fss
-> in-house point-in-time factor engine
-> quantstats + PyPortfolioOpt/Riskfolio-Lib
-> KIS official API reference + internal paper-only KIS client
-> optional read-only MCP for analyst/copilot mode
```

## II. 전체 생태계 지도

### A. 투자전략/퀀트 전략 구현 저장소

| 자산 | 관심도 | 최근성 | 라이선스 | 전략 타당성 | 코드 재사용성 | MVP 판단 |
|---|---:|---|---|---|---|---|
| [microsoft/qlib](https://github.com/microsoft/qlib) | 43,144 stars | pushed 2026-04-22 | MIT | ML/AI 퀀트 연구 플랫폼으로 구조는 좋지만 국내 데이터 어댑터 필요 | 중간. 무겁고 학습 비용 큼 | Phase 2 연구용 |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | 15,178 | pushed 2026-04-05 | MIT | RL 기반. 투자전략 타당성은 MVP 기준 약함 | 낮음. 국내 주식 실전 검증과 거리 있음 | 참고만 |
| [stefan-jansen/machine-learning-for-trading](https://github.com/stefan-jansen/machine-learning-for-trading) | 17,342 | pushed 2024-08-18 | 라이선스 확인 필요 | ML/factor 연구 교육 자료로 유용 | 낮음. 코드 직접 사용보다 개념 참조 | 참고만 |
| [je-suis-tm/quant-trading](https://github.com/je-suis-tm/quant-trading) | 9,867 | pushed 2024-04-14 | Apache-2.0 | 전략 cookbook. 검증 품질은 전략별 편차 큼 | 낮음-중간 | 참고만 |
| [koreainvestment/open-trading-api strategy_builder](https://github.com/koreainvestment/open-trading-api) | 1,393 | pushed 2026-03-18 | 명시 라이선스 없음 | 기술지표 preset은 교육/샘플 수준 | 중간. API 흐름 참조용 | 주문 제외, 참고 |
| [tanish35/Momentum-Investing](https://github.com/tanish35/Momentum-Investing) | 소규모 | crawled 2026-05 | GitHub 확인 필요 | 모멘텀+레짐+저변동성 아이디어는 연결성 있음 | 낮음. 국내 데이터 변환 필요 | 아이디어 참고 |

판단: A 범주는 직접 가져오기보다 전략 가설, 검증 항목, 리포트 구조를 참고한다. 국내 MVP의 핵심 전략 코드는 in-house로 유지해야 lookahead, 거래비용, 상장폐지, 시장구분, 공시 차단을 통제할 수 있다.

### B. 국내 주식 데이터 수집/정제 저장소

| 자산 | 관심도 | 최근성 | 라이선스 | API 최신성/유지보수 | MVP 판단 |
|---|---:|---|---|---|---|
| [sharebook-kr/pykrx](https://github.com/sharebook-kr/pykrx) | 1,005 | pushed 2026-05-04, PyPI 1.2.8 2026-05-04 | repo license 미검출, PyPI MIT | KRX 스크래핑 기반. PER/PBR/배당/수급/공매도 등 국내 특화 | 핵심 채택 |
| [FinanceData/FinanceDataReader](https://github.com/FinanceData/FinanceDataReader) | 1,483 | pushed 2026-05-13, PyPI 0.9.202 2026-05-13 | MIT | KRX 종목, 가격, 지수, 환율, 상폐/관리종목 목록까지 폭넓음 | 핵심 채택 |
| [FinanceData/OpenDartReader](https://github.com/FinanceData/OpenDartReader) | 448 | pushed 2026-05-16, PyPI 0.3.2 2026-05-15 | MIT | OpenDART API를 종목코드/기업명 중심으로 쉽게 래핑 | 핵심 채택 |
| [josw123/dart-fss](https://github.com/josw123/dart-fss) | 368 | pushed 2025-12-03 | MIT | OpenDART + DART 재무제표 추출. 호출 제한 주의 | 핵심/보조 채택 |
| [FinanceData/marcap](https://github.com/FinanceData/marcap) | 282 | pushed 2026-02-23 | 명시 라이선스 없음 | 1995-2026 일별 시총 데이터셋. 데이터 출처/재배포 조건 확인 필요 | 연구용만 |
| [KRX Open API](https://openapi.krx.co.kr/) | 공식 | 2026 공지 확인 | 약관 기반 | 인증키/승인 기반 공식 통계 API | 공식 소스 |
| [OpenDART](https://opendart.fss.or.kr/) | 공식 | 2026 변동내역 공지 확인 | 약관 기반 | 공시/재무 API 공식 소스 | 공식 소스 |

판단: B가 MVP의 핵심이다. 추천 엔진의 품질은 AI Agent보다 point-in-time 데이터 정합성에 달려 있다.

### C. 한국 증권사 API 자동매매/주문 저장소

| 자산 | 관심도 | 최근성 | 라이선스 | 주문 위험 | MVP 판단 |
|---|---:|---|---|---|---|
| [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api) | 1,393 | pushed 2026-03-18 | 명시 라이선스 없음 | 공식 샘플이나 손실 면책, 실전 주문 가능 API | API 기준서/샘플 참조 |
| [Soju06/python-kis](https://github.com/Soju06/python-kis) | 275 | pushed 2026-02-21, release v2.1.6 2025-10-13 | MIT | REST/웹소켓 래퍼. 주문 기능은 paper lock 필요 | 보조 채택 후보 |
| [unohee/kis-agent](https://github.com/unohee/kis-agent) | 18 | release v1.6.1 2026-04-07 | MIT | CLI/LLM/MCP 지향. 신생 프로젝트 | 관찰/실험 |
| [sharebook-kr/mojito](https://github.com/sharebook-kr/mojito) | 89 | pushed 2024-02-20 | MIT | KIS wrapper. 최신성은 python-kis보다 약함 | 참고 |
| [younghwan91/kiwoom-rest-api](https://github.com/younghwan91/kiwoom-rest-api) | 1 | pushed 2026-03-30 | MIT | Kiwoom REST wrapper. 매우 신생, 검증 부족 | 관찰 |
| [elbakramer/koapy](https://github.com/elbakramer/koapy) | 222 | pushed 2023-02-11 | MIT/Apache-2.0/GPL-3.0-or-later | Kiwoom OpenAPI+는 Windows/COM/HTS 의존 | 참고 |
| [sharebook-kr/pykiwoom](https://github.com/sharebook-kr/pykiwoom) | 112 | pushed 2025-07-09 | Apache-2.0 | Kiwoom OpenAPI+ wrapper. 운영 제약 큼 | 참고 |
| [breadum/kiwoom](https://github.com/breadum/kiwoom) | 180 | pushed 2025-09-14 | MIT | 32-bit Windows/OpenAPI+ 필수 | 참고 |
| [xorrhks0216/LsApiHelper](https://github.com/xorrhks0216/LsApiHelper) | 1 | GitHub page 4 commits | MIT | LS REST/WebSocket wrapper. 극초기 | 관찰 |

판단: 주문 인프라는 가져오더라도 `paper_only`, `dry_run`, `order_intent`까지만 허용한다. 자동 실전 주문은 MVP 밖이다. KIS가 가장 현대적이고 cross-platform 친화적이며, Kiwoom OpenAPI+ 계열은 한국어 자료가 많지만 32-bit Windows/HTS/COM 운영 리스크가 크다.

### D. 백테스트/팩터리서치 프레임워크

| 자산 | 관심도 | 최근성 | 라이선스 | 장점 | MVP 판단 |
|---|---:|---|---|---|---|
| [pmorissette/bt](https://github.com/pmorissette/bt) | 2,869 | pushed 2026-05-05, PyPI 1.2.0 2026-04-25 | MIT | 포트폴리오 리밸런싱 백테스트가 간단 | 보조 채택 |
| [ranaroussi/quantstats](https://github.com/ranaroussi/quantstats) | 7,129 | pushed 2026-01-13 | Apache-2.0 | 성과 리포트/지표 생성 | 채택 |
| [PyPortfolio/PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | 5,725 | pushed 2026-04-20, PyPI 1.6.0 2026-02-26 | MIT | 포트폴리오 최적화 | 보조 채택 |
| [dcajasn/Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | 4,190 | pushed 2026-05-08 | BSD-3-Clause | 위험예산, CVaR, HRP 등 | 보조 채택 |
| [alphalens / alphalens-reloaded](https://github.com/quantopian/alphalens) | 4,270 | repo pushed 2024-02-12, reloaded PyPI 2025-06-02 | Apache-2.0 | factor IC, turnover, quantile 분석 | Phase 2 채택 |
| [stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded) | 1,769 | pushed 2026-01-06 | Apache-2.0 | event-driven backtest | 무겁다. 필요 시 |
| [mementum/backtrader](https://github.com/mementum/backtrader) | 21,586 | pushed 2024-08-19 | GPL-3.0 | 성숙한 backtest engine | 라이선스/구조상 참고 |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | 7,578 | pushed 2026-04-25 | Commons Clause 포함 | 대규모 벡터화 실험 | 상업/배포 리스크 |
| [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | 8,378 | pushed 2025-12-20 | AGPL-3.0 | 단일 전략 backtest 편리 | 라이선스상 피함 |
| [edtechre/pybroker](https://github.com/edtechre/pybroker) | 3,323 | pushed 2026-05-11 | Commons Clause 포함 | ML/walk-forward 편의 | 상업/배포 리스크 |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | 19,029 | pushed 2026-05-15 | Apache-2.0 | 강력한 엔진 | C#/플랫폼 무게 큼 |

판단: 지금은 in-house 검증기가 우선이다. 외부 프레임워크는 성과 분석과 포트폴리오 보조 기능부터 붙인다. 대형 엔진을 코어로 바꾸면 국내 데이터 정합성 검증보다 프레임워크 적응 비용이 커진다.

### E. AI 기반 주식 분석 Agent/Skill/MCP

| 자산 | 관심도 | 최근성 | 라이선스 | 강점 | MVP 판단 |
|---|---:|---|---|---|---|
| [koreainvestment/open-trading-api MCP](https://github.com/koreainvestment/open-trading-api) | 1,393 | pushed 2026-03-18 | 명시 라이선스 없음 | KIS 공식 샘플, LLM/MCP 폴더 포함 | API 참조 |
| [jjlabsio/korea-stock-mcp](https://github.com/jjlabsio/korea-stock-mcp) | 135 | pushed 2026-05-15 | ISC | DART/KRX 공식 API 기반 MCP | read-only 실험 |
| [sharebook-kr/pykrx-mcp](https://github.com/sharebook-kr/pykrx-mcp) | 3 | pushed 2026-02-01, PyPI 0.1.3 2026-01-31 | MIT | pykrx를 MCP 도구화 | read-only 실험 |
| [Mrbaeksang/korea-stock-analyzer-mcp](https://github.com/Mrbaeksang/korea-stock-analyzer-mcp) | 17 | pushed 2025-09-20 | MIT | 한국 주식 분석 템플릿 | 참고 |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 76,732 | pushed 2026-05-17 | Apache-2.0 | 다중 에이전트 투자 리서치 구조 | 아키텍처 참고 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | 58,949 | pushed 2026-05-14 | 명시 라이선스 없음 | 투자자 persona 기반 agent 예시 | 코드 복사 금지 |
| [AI4Finance-Foundation/FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | 6,985 | pushed 2026-05-10 | Apache-2.0 | 금융 리포트/분석 agent | 참고 |
| [OpenBB-finance/OpenBB](https://github.com/OpenBB-finance/OpenBB) | 67,737 | pushed 2026-05-18 | AGPL-3.0 | 금융 데이터/AI agent 플랫폼 | 라이선스 주의 |
| [wshobson/maverick-mcp](https://github.com/wshobson/maverick-mcp) | 551 | pushed 2026-05-13 | MIT | MCP 기반 분석/포트폴리오 도구 | 미국주식 중심 참고 |

판단: AI/MCP는 데이터 조회와 설명 계층으로 제한한다. `추천 점수 산출`, `승격`, `주문 의도 생성`은 결정론적 코드와 검증 로그가 담당해야 한다.

### F. 완성형 주식 추천/자동매매 공개 프로젝트

| 자산 | 관심도 | 최근성 | 라이선스 | 실사 의견 | MVP 판단 |
|---|---:|---|---|---|---|
| [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api) | 1,393 | 2026-03-18 | 명시 라이선스 없음 | 공식 샘플이지만 손실 면책, 샘플 코드 성격 | 참조 |
| [lani009/Kiwoom-ATS](https://github.com/lani009/Kiwoom-ATS) | 18 | pushed 2025-05-06 | Apache-2.0 | Kiwoom 자동매매 예제 | 참고만 |
| [stock-price-calculator/tradingbot](https://github.com/stock-price-calculator/tradingbot) | 25 | pushed 2023-09-30 | 명시 라이선스 없음 | Kiwoom 자동매매. 유지보수/라이선스 약함 | 사용 금지 |
| [dongzooo/KiwoomAPI-AutoTrade](https://github.com/dongzooo/KiwoomAPI-AutoTrade) | 4 | pushed 2023-03-23 | 명시 라이선스 없음 | 단순 지지/저항 자동매매 | 사용 금지 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | 58,949 | 2026-05-14 | 명시 라이선스 없음 | 흥미 높은 agent 데모. 전략 검증과 국내 적합성 약함 | 참고만 |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | 76,732 | 2026-05-17 | Apache-2.0 | agent orchestration 참고 가능 | 참고 |

판단: 완성형 자동매매 프로젝트는 코드 재사용 가치가 낮다. 추천/주문 안전성을 직접 검증하기 어렵고, 대부분 실전 주문 위험을 충분히 분리하지 않는다.

### G. 한국어 커뮤니티/국내 개발자 생태계에서 많이 회자되는 프로젝트

| 축 | 대표 자산 | 실사 판단 |
|---|---|---|
| 국내 데이터 수집 | `pykrx`, `FinanceDataReader`, `OpenDartReader`, `dart-fss` | 가장 많이 재사용되는 실무 기반. MVP 핵심 |
| 국내 증권사 주문 | KIS 공식 샘플, `python-kis`, Kiwoom OpenAPI+ wrappers | 자료는 많지만 실전 주문은 금지 |
| Kiwoom 생태계 | `koapy`, `pykiwoom`, `breadum/kiwoom`, 각종 ATS 예제 | 한국어 자료 풍부. Windows/32-bit/HTS 제약 |
| 교육/책 예제 | [INVESTAR/StockAnalysisInPython](https://github.com/INVESTAR/StockAnalysisInPython), [wikibook/quant](https://github.com/wikibook/quant) | 학습/아이디어 참고. MVP 코드 직접 사용 가치는 낮음 |
| AI/MCP | `korea-stock-mcp`, `pykrx-mcp`, KIS MCP | read-only 분석 보조로 유망 |
| 커뮤니티 신호 | 블로그/강의/검색 결과에서 `pykrx`, FDR, Kiwoom, KIS가 반복 등장 | 관심도 신호일 뿐 검증 근거는 아님 |

## III. Top 추천 자산 20개

점수 기준:

- 전략 타당성: 투자 논리가 검증 가능한가
- 코드 재사용성: KR DayPilot에 코드/패턴/CLI/API를 안전하게 붙일 수 있는가
- 국내 MVP 적합성: 국내 주식, KRX/DART/KIS, paper-only 운영에 맞는가

| 순위 | 자산 | 범주 | 관심도 | 라이선스 | 전략 타당성 | 코드 재사용성 | 국내 MVP 적합성 | 결론 |
|---:|---|---|---:|---|---|---|---|---|
| 1 | `sharebook-kr/pykrx` | B/G | 1,005 | PyPI MIT, repo license 미검출 | 보조 데이터 | 높음 | 매우 높음 | 핵심 데이터 |
| 2 | `FinanceData/FinanceDataReader` | B/G | 1,483 | MIT | 보조 데이터 | 높음 | 매우 높음 | 핵심 데이터 |
| 3 | `FinanceData/OpenDartReader` | B/G | 448 | MIT | 공시/재무 보조 | 높음 | 매우 높음 | 핵심 DART |
| 4 | `josw123/dart-fss` | B/G | 368 | MIT | 재무제표 보조 | 중간-높음 | 높음 | DART 보강 |
| 5 | `koreainvestment/open-trading-api` | C/E/F/G | 1,393 | 명시 라이선스 없음 | 주문전략은 약함 | 참조 높음 | 높음 | 공식 참조, 복사 금지 |
| 6 | `Soju06/python-kis` | C/G | 275 | MIT | 주문/시세 보조 | 중간 | 높음 | paper context 후보 |
| 7 | `FinanceData/marcap` | B | 282 | 명시 라이선스 없음 | 시총/유니버스 보조 | 중간 | 높음 | 연구용, 라이선스 확인 |
| 8 | `ranaroussi/quantstats` | D | 7,129 | Apache-2.0 | 성과평가 | 높음 | 높음 | 리포트 채택 |
| 9 | `PyPortfolio/PyPortfolioOpt` | D | 5,725 | MIT | 포트폴리오 보조 | 높음 | 중간-높음 | sizing/constraint |
| 10 | `dcajasn/Riskfolio-Lib` | D | 4,190 | BSD-3-Clause | 리스크 보조 | 중간 | 중간-높음 | risk allocation |
| 11 | `pmorissette/bt` | D | 2,869 | MIT | 포트폴리오 backtest | 중간 | 중간 | 보조 엔진 |
| 12 | `alphalens-reloaded`/`alphalens` | D | 4,270 repo | Apache-2.0 | 팩터 검증 | 중간 | 중간 | Phase 2 factor IC |
| 13 | `jjlabsio/korea-stock-mcp` | E/G | 135 | ISC | AI 분석 보조 | 중간 | 높음 | read-only MCP |
| 14 | `sharebook-kr/pykrx-mcp` | E/G | 3 | MIT | AI 데이터 조회 | 중간 | 높음 | read-only MCP |
| 15 | `unohee/kis-agent` | C/E | 18 | MIT | API/LLM 도구 | 중간 | 중간 | 관찰/실험 |
| 16 | `stefan-jansen/zipline-reloaded` | D | 1,769 | Apache-2.0 | event-driven 검증 | 중간 | 낮음-중간 | 필요 시 |
| 17 | `microsoft/qlib` | A/D | 43,144 | MIT | ML quant 연구 | 낮음-중간 | 낮음-중간 | Phase 2 |
| 18 | `younghwan91/kiwoom-rest-api` | C | 1 | MIT | 주문/시세 보조 | 낮음-중간 | 중간 | Kiwoom REST 관찰 |
| 19 | `elbakramer/koapy`/`pykiwoom` | C/G | 222/112 | permissive 또는 multi-license | 주문/시세 보조 | 중간 | 낮음-중간 | Windows 제약 참고 |
| 20 | `TauricResearch/TradingAgents` | E/F | 76,732 | Apache-2.0 | AI agent 구조 | 낮음 | 낮음 | 구조 참고 |

## IV. 당장 가져다 쓸 만한 것 Top 10

### 1. pykrx

- 용도: PER/PBR/배당, OHLCV, 투자자별 거래대금, 공매도, ETF/지수 등 국내 특화 데이터.
- 사용 방식: 이미 `pyproject.toml`에 들어간 핵심 의존성 유지.
- 주의: KRX 스크래핑/세션 의존. 수집 실패, 컬럼명 변경, 장마감 후 데이터 지연을 검사해야 한다.

### 2. FinanceDataReader

- 용도: 가격/지수/환율/상폐/관리종목/시총 universe 보강.
- 사용 방식: 데이터 어댑터로 추가하되, raw와 normalized를 분리.
- 주의: 소스별 데이터 출처가 섞이므로 source metadata가 필수.

### 3. OpenDartReader

- 용도: 종목코드 기반 공시 목록, 재무제표, 주요사항보고서 접근.
- 사용 방식: DART Event Impact Gate와 RG2 펀더멘털 수집 보강.
- 주의: API key, 호출 제한, 정정공시, 보고서 발표 시점 기준 point-in-time 처리.

### 4. dart-fss

- 용도: OpenDART보다 깊은 재무제표/XBRL 추출 보조.
- 사용 방식: OpenDartReader로 부족한 계정/재무제표 품질을 보강.
- 주의: 분당 요청 제한과 DART HTML/XBRL 구조 변경.

### 5. KIS 공식 open-trading-api

- 용도: REST, WebSocket, 인증, 국내주식 시세/주문 API의 공식 샘플과 최신 변경 추적.
- 사용 방식: 코드 복사보다 API contract 확인용. 내부 `kis_client.py`를 유지하며 필요한 endpoint만 구현.
- 주의: 명시 라이선스 없음. 실전 주문은 코드 경로에서 차단.

### 6. python-kis

- 용도: KIS REST/WebSocket client 패턴, reconnect, rate limit, typed API 참고.
- 사용 방식: 직접 의존성 후보로 검토 가능. 우선은 내부 client와 비교 평가.
- 주의: 주문 API는 paper lock, credential isolation, dry-run guard를 걸어야 한다.

### 7. quantstats

- 용도: 전략별 CAGR, Sharpe, drawdown, 월별 수익, tearsheet 생성.
- 사용 방식: `output/research_gate2`와 `output/value_momentum_mvp` 성과 리포트 보강.
- 주의: 한국 세금/거래비용/슬리피지 반영은 KR DayPilot 쪽에서 먼저 계산해야 한다.

### 8. bt

- 용도: 월간/주간 리밸런싱 포트폴리오 실험.
- 사용 방식: in-house 검증기의 결과를 cross-check하는 보조 엔진.
- 주의: point-in-time universe와 거래비용 모델은 자체 통제.

### 9. PyPortfolioOpt

- 용도: equal weight 이후의 risk cap, sector cap, volatility-based weight 실험.
- 사용 방식: `paper_only` 이전에는 보조 분석으로만 사용.
- 주의: 최적화가 과거 공분산에 과적합될 수 있다.

### 10. Riskfolio-Lib

- 용도: CVaR, risk parity, HRP, drawdown-aware allocation 실험.
- 사용 방식: Alpha Ensemble의 포트폴리오 레이어 후보.
- 주의: 전략 알파를 만들어 주는 도구가 아니라 리스크 배분 도구다.

## V. 이전 투자전략 조사 결과와의 연결성 분석

이전 결론:

- DART Event Impact Gate 단독은 투자 추천 엔진으로 약함.
- 추천 엔진은 Alpha Ensemble Recommendation Gate 방향이 적합함.
- 핵심 전략군은 다요인 펀더멘털 랭킹, 가치+모멘텀, 저변동성+추세, 공시 이벤트 필터, 외국인/기관 수급 보조.
- 실전 주문은 금지하고 paper_only/매매보조 중심으로 설계.

연결성:

| 이전 전략군 | 필요한 자산 | 오픈소스 연결 | 판단 |
|---|---|---|---|
| 다요인 펀더멘털 랭킹 | PER/PBR/ROE/ROA/영업이익률/시총/유동성 | pykrx, FinanceDataReader, OpenDartReader, dart-fss | 최우선 구현 |
| 가치+모멘텀 | 가치지표 + 6/12개월 상대 모멘텀 | pykrx, FDR, marcap, in-house engine | 현 MVP와 직접 연결 |
| 저변동성+추세 | 60/120일 변동성, 이동평균, 시장 레짐 | pykrx/FDR + quantstats/bt | RG2/Phase 2 |
| 공시 이벤트 필터 | 공시 목록, 정정/소송/유증/감사의견 등 | OpenDartReader, dart-fss, korea-stock-mcp | 알파보다 risk gate |
| 외국인/기관 수급 | 투자자별 순매수, 거래대금, 프로그램 | pykrx, KRX, KIS/LS 보조 | 보조 feature, observe-only에서 시작 |
| 실전 주문/체결 현실성 | 호가, 체결, 계좌, 주문 상태 | KIS official, python-kis, KIS WebSocket | paper ledger까지만 |
| AI 분석/설명 | 공시/재무 요약, 리포트 초안 | korea-stock-mcp, pykrx-mcp, TradingAgents 구조 | 추천권한 없음 |

핵심은 외부 AI agent가 아니라 데이터/검증 계층이다. AI는 “왜 이 후보가 올라왔는지” 설명하고 공시/리스크를 요약하는 보조 역할이 맞다.

## VI. MVP 개발 관점의 추천 조합 3개

### 조합 1. 보수적 Alpha Ensemble MVP

```text
pykrx + FinanceDataReader + OpenDartReader/dart-fss
-> in-house point-in-time factor engine
-> quantstats report
-> HTML decision report
```

용도:

- 현재 `VALUE_MOMENTUM_MVP`와 `RESEARCH_GATE_2`를 안정화.
- 다요인 점수, 공시 차단, 유동성 필터, 거래비용 차감, 월간/주간 리밸런싱 검증.

장점:

- 라이선스/운영 리스크가 낮다.
- 실전 주문을 열지 않아 안전하다.
- 국내 데이터 특화가 가장 쉽다.

단점:

- 실시간 체결 현실성은 약하다.

추천도: 가장 높음.

### 조합 2. Paper Decision Desk MVP

```text
조합 1
-> KIS official API reference
-> internal KIS client or python-kis comparison
-> orderbook/trade-strength snapshot
-> paper order intent + paper fill ledger
```

용도:

- 추천 후보에 대해 장중 호가/체결강도/시장 상태를 붙여 실제 매매보조 화면으로 확장.
- 주문은 넣지 않고 `paper_order_intent`, `paper_fill`, `cancel_condition`만 기록.

장점:

- 실제 운영에 가까운 데이터 품질 검증 가능.
- 매매보조 앱으로 사용자 가치가 크다.

단점:

- credential, rate limit, WebSocket 복구, 장중 장애 대응을 구현해야 한다.

추천도: 조합 1 안정화 후 진행.

### 조합 3. AI Analyst Copilot MVP

```text
조합 1 또는 2
-> korea-stock-mcp or pykrx-mcp read-only
-> DART/재무/수급 설명 생성
-> final decision remains deterministic
```

용도:

- 후보별 “추천 사유/차단 사유/데이터 결측/공시 리스크”를 사람이 읽기 좋게 요약.
- 공시 원문과 재무 수치를 설명하는 보조 analyst.

장점:

- 사용자가 후보를 검토하는 속도가 빨라진다.
- LLM을 추천권한 없이 안전하게 붙일 수 있다.

단점:

- MCP 서버 품질이 아직 초기.
- 자연어 답변은 반드시 원천 데이터 링크와 수치 검증 로그를 붙여야 한다.

추천도: read-only로 제한하면 유용.

## VII. 가져다 쓰기 전 리스크

### 1. 라이선스 리스크

- 명시 라이선스 없음: `koreainvestment/open-trading-api`, `FinanceData/marcap`, `virattt/ai-hedge-fund`, 일부 Kiwoom 자동매매 프로젝트. 코드 복사 금지. 참조만.
- AGPL: `OpenBB`, `backtesting.py`. 서비스/배포 시 소스 공개 의무가 생길 수 있으므로 MVP 코어 의존성에서 제외.
- Commons Clause: `vectorbt`, `PyBroker`. 유료/상업 서비스 가치가 해당 기능에 의존하면 제한될 수 있으므로 직접 의존성에서 제외.
- GPL: `backtrader`. 배포 모델에 따라 의무가 생길 수 있어 코어 의존성은 피한다.
- 데이터셋 라이선스: `marcap`은 저장소 라이선스가 명확하지 않다. 연구용으로만 보고, 재배포/제품 포함 전 별도 확인.

### 2. API 최신성/유지보수 리스크

- KRX/pykrx/FDR은 웹 구조, 세션, 컬럼명이 바뀌면 깨질 수 있다.
- DART는 정정공시, XBRL 계정명, 연결/별도 재무제표 구분이 어렵다.
- KIS는 2026년 공지 기준 호출 제한과 WebSocket 정책 변경을 계속 확인해야 한다.
- Kiwoom OpenAPI+는 Windows, 32-bit Python, HTS/KOA Studio, 보안 프로그램, 로그인 상태 의존성이 크다.
- LS REST/OpenAPI는 유망하지만 Python wrapper 생태계가 아직 얕다.

### 3. 전략 타당성 리스크

- Star가 많은 AI trading repo는 전략 검증 근거가 아니다.
- LLM agent가 만든 buy/sell 의견은 백테스트된 알파가 아니다.
- RL/ML 전략은 데이터 누수, 생존편향, 과최적화 위험이 크다.
- 국내 소형주/저유동성 종목은 호가 공백, VI, 거래정지, 단기과열, 관리종목 리스크가 크다.

### 4. 실전 주문 리스크

- 주문 API는 단순 HTTP 호출 문제가 아니라 계좌/자금/정정/취소/부분체결/거부/중복주문/장애복구 문제다.
- MVP는 주문 API를 직접 호출하지 않고 `order_intent`와 `paper_fill`만 기록해야 한다.
- 실전 주문 경로는 별도 승인, 별도 config, 별도 테스트 계좌, kill switch, daily loss limit, human confirmation이 있기 전까지 닫는다.

### 5. 데이터 품질 리스크

- 모든 feature는 `as_of_time`, `source_bas_dt`, `collected_at`, `source`를 가져야 한다.
- 재무제표는 발표일 이후에만 사용해야 하며, 회계기간 말일 기준으로 선반영하면 lookahead가 된다.
- 상장폐지/관리종목/거래정지/합병/액면분할/권리락 처리가 필요하다.
- 수급 데이터는 장마감 후 확정 시간이 다를 수 있다.

## VIII. 최종 결론

KR DayPilot에 바로 필요한 것은 “큰 AI 자동매매 시스템”이 아니라, 국내 데이터 정합성과 검증 가능한 factor engine이다.

최종 권고:

1. 코어는 현재 방향대로 `Value/Quality + Momentum MVP`와 `Research Gate 2`를 강화한다.
2. 데이터는 `pykrx`, `FinanceDataReader`, `OpenDartReader`, `dart-fss`를 중심으로 normalize한다.
3. 성과/리스크 리포트는 `quantstats`, 포트폴리오 보조는 `PyPortfolioOpt` 또는 `Riskfolio-Lib`로 붙인다.
4. KIS는 주문 자동화가 아니라 호가/체결강도/paper fill 현실성 검증에 쓴다.
5. AI/MCP는 read-only analyst/copilot로 제한한다.
6. `OpenBB`, `vectorbt`, `PyBroker`, `backtesting.py`, `backtrader`는 라이선스와 구조 리스크 때문에 MVP 코어 의존성으로 넣지 않는다.
7. 완성형 자동매매 공개 프로젝트는 코드 재사용보다 실패 사례/위험 패턴 참고에 가깝다.

따라서 다음 구현 우선순위는:

```text
1. FDR/OpenDartReader/dart-fss adapter 추가 검토
2. RG2/value-momentum point-in-time 데이터 품질 점검 강화
3. quantstats 성과 리포트 추가
4. KIS paper-only execution context 확대
5. read-only MCP/AI analyst를 별도 실험으로 격리
```

## Source Notes

주요 확인 출처:

- KIS Developers 공식 포털: https://apiportal.koreainvestment.com/
- KIS 공식 샘플 저장소: https://github.com/koreainvestment/open-trading-api
- KRX Open API: https://openapi.krx.co.kr/
- KRX 데이터 수신/계약 안내: https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp
- OpenDART 공식: https://opendart.fss.or.kr/
- FinanceDataReader: https://github.com/FinanceData/FinanceDataReader
- OpenDartReader: https://github.com/FinanceData/OpenDartReader
- dart-fss: https://github.com/josw123/dart-fss
- pykrx: https://github.com/sharebook-kr/pykrx
- korea-stock-mcp: https://github.com/jjlabsio/korea-stock-mcp
- pykrx-mcp PyPI: https://pypi.org/project/pykrx-mcp/
- LS Open API: https://openapi.ls-sec.co.kr/about-openapi
- Kiwoom REST wrapper: https://github.com/younghwan91/kiwoom-rest-api
- KOAPY: https://github.com/elbakramer/koapy
- GitHub/PyPI metadata was checked on 2026-05-18 for stars, pushed dates, releases, and licenses where available.
