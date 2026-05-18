# Spec: KR DayPilot v2 국내주식 추천 웹앱 재설계

## 현재 결론

처음부터 다시 만드는 방향은 맞지만, "웹앱 먼저"가 아니라 "검증 가능한 추천 엔진을 단순한 웹 의사결정 화면에 보여주는 방식"이어야 한다.

두 공유 보고서의 공통 결론은 다음과 같다.

- 단타, 스캘핑, VI, 상한가 후속파동, 뉴스 속도전은 개인 투자자에게 구조적으로 불리하고 검증보다 성공담이 앞선다.
- 구현 우선순위는 펀더멘털/퀀트, 가치+모멘텀, 저변동성, 수급 보조, 공시 이벤트 필터 쪽이 높다.
- 완전 자동매매보다 추천 프로그램과 매매보조 프로그램이 현실적이다.
- 매수 버튼보다 "왜 후보인지, 어떤 근거로 차단됐는지, 어떤 검증을 통과했는지"를 보여주는 의사결정 데스크가 먼저다.

첨부 및 로컬 저장된 두 보고서도 같은 결론을 뒷받침한다.

- `deep-research-report.md`: "펀더멘털 다요인 랭킹 + 리스크/희석 이벤트 필터 + 일봉 차트 타점 보조"를 가장 현실적인 전략 형태로 제시한다.
- `deep-research-report.md`: 프로그램화 후보 Top5를 다요인 스마트베타, 가치+모멘텀 스윙, 저변동성+추세 필터, 공시 이벤트 알림, 외인/기관 수급 보조로 정리한다.
- `국내 주식 투자 기법 검증 및 자동화 전략 연구.pdf`: 단기 차트/돌파/이평선 단독 기법은 거래비용과 슬리피지 반영 후 취약하며, 가치 및 모멘텀 결합, 공시 이벤트 드리븐, 단기 반전 전략을 상대적으로 유효한 축으로 제시한다.
- `국내 주식 투자 기법 검증 및 자동화 전략 연구.pdf`: 초단타 완전 자동매매보다 "Recommendation & Trading Assistant System" 형태가 현실적이라고 결론낸다.

로컬 검증 결과도 같은 방향을 가리킨다.

- 기존 단기/스윙 계열 최신 요약은 성공률 19.75%, 평균 순수익률 -0.948%, research_pass=false였다.
- Research Gate 1은 9개 전략군, 45개 전략+집행 조합, paper_filled 3,572건을 비교했지만 research_gate_pass=false였다.
- 가장 좋아 보인 `momentum_pullback_deep_rebound + strength_follow`도 평균 순수익률 +1.15%였지만 체결 표본이 4건뿐이라 채택 불가다.
- 따라서 현재 실패는 UI 문제가 아니라 알파, 표본, 데이터 가용성, 검증 기준 문제다.

## Assumptions

1. 이 앱은 국내주식 "추천/매매보조" 웹앱이며, 실전 자동주문 앱이 아니다.
2. 첫 버전은 로컬 파일 기반 배치 실행 + 정적 HTML 웹앱으로 만든다.
3. 현재 사용 가능한 데이터만 쓴다: `data/kr_stock_price_history.csv`, `data/kr_universe.csv`, `data/eod_context/*`, `data/performance/trade_log.csv`, 기존 실험 산출물.
4. 재무제표/컨센서스/FnGuide 같은 유료 데이터는 아직 쓰지 않는다. 해당 팩터는 "데이터 없음"으로 표시한다.
5. 기존 코드와 산출물은 보존한다. v2는 별도 모듈과 별도 출력 폴더에 만든다.
6. 추천 화면에는 `매수` 대신 `research_only`, `paper_only_candidate`, `blocked`, `data_unavailable` 같은 상태를 표시한다.
7. 실전 주문, 계좌 연동, 글로벌 Codex 설정 변경, 비밀값 출력은 범위 밖이다.

## Objective

국내주식 후보를 매일 0~10개로 압축하되, 추천보다 검증 근거와 차단 사유를 우선 표시하는 웹앱을 만든다.

사용자는 다음 질문에 답을 얻어야 한다.

- 오늘 데이터가 신뢰 가능한가?
- 어떤 전략군이 후보를 냈는가?
- 각 후보는 어떤 팩터와 이벤트 때문에 점수가 높거나 낮은가?
- 해당 전략군은 과거 OOS/워크포워드 검증에서 어떤 성과였는가?
- 오늘은 매매 검토 가능일인가, 아니면 데이터/시장/리스크 사유로 쉬어야 하는가?

## MVP 범위

### 포함

- EOD 기준 후보 산출
- 데이터 신선도/누락/커버리지 점검
- 전략별 후보 점수와 차단 사유
- 간단한 워크포워드 검증 요약
- 후보별 진입 구간, 손절, 목표, 보유기간 제안
- HTML 의사결정 대시보드
- 출력 재현성을 위한 JSON/CSV/HTML 동시 저장

### 제외

- 실전 자동주문
- 분봉 스캘핑 전략
- 실시간 뉴스 속도전
- 상한가/VI 추격 자동매매
- 유료 데이터 의존 팩터의 가짜 대체값 생성
- 검증 실패 전략을 "추천"으로 포장하는 UI

## Strategy Design

### v2-001: EOD 멀티팩터 라이트

현재 로컬 데이터로 구현 가능한 핵심 전략이다.

입력:

- 가격/거래대금: 일봉 OHLCV, 거래대금, 시가총액
- 모멘텀: 20일, 60일 수익률
- 반전/과열: 1일, 3일, 5일 수익률
- 변동성: 20일 변동성
- 유동성: 20일 평균 거래대금, 당일 거래대금
- 섹터 대체값: 현재 `sector`가 제한적이면 시장/동일 그룹 프록시만 사용
- 수급: 외국인, 기관, 개인 순매수 z-score
- 이벤트 리스크: DART 공시 risk_flag, event_type

점수 방향:

- 가점: 중기 추세 양호, 최근 과도하지 않은 눌림, 거래대금 충분, 종가 위치 회복, 외국인/기관 수급 안정
- 감점: 5일 급등 추격, 1일 급락, 과도한 변동성, 공시 리스크, 유동성 부족, 시총/가격 하한 미달

상태:

- `blocked`: 데이터 stale, 리스크 공시, 유동성 부족, 가격/시총 하한 미달
- `research_only`: 후보는 있으나 전략 검증 미통과
- `paper_only_candidate`: 검증 조건을 충족한 전략군에서 나온 후보

보고서 원형 전략과의 관계:

- 다요인 스마트베타와 가치+모멘텀 전략은 재무 데이터가 없으면 완성도가 낮다.
- 따라서 v2 첫 구현은 가격, 변동성, 거래대금, 수급, 공시 리스크만으로 가능한 "멀티팩터 라이트"로 시작한다.
- 재무제표 데이터가 확보되면 수익성, 투자, 가치, 퀄리티 팩터를 별도 feature group으로 추가한다.

### v2-002: DART 이벤트 게이트

처음에는 알파 엔진이 아니라 리스크 게이트로 둔다.

기본 분류:

- positive_watch: 자사주 취득, 수주/공급계약, 신규시설투자, 실적 개선성 공시
- risk_block: 유상증자, CB/BW 운영자금성 조달, 감사/상장폐지/거래정지, 소송, 최대주주 리스크
- neutral: 대량보유, 단순 정정, 정기보고서 등

규칙:

- risk_block은 매수 후보에서 제외한다.
- positive_watch는 단독 매수 근거가 아니라 점수 보조로만 쓴다.
- 텍스트 파싱 확신도가 낮으면 `needs_manual_review`로 표시한다.

### v2-003: 종가 수급 + 단기 반전 보조

보고서와 기존 RG1에서 모두 언급된 한국 시장 특화 보조 전략이다.

규칙:

- 최근 3~5일 과매도
- 전일/당일 기준 외국인 또는 기관 순매수 안정
- 리스크 공시 없음
- 거래대금과 시총 하한 통과
- 다음 거래일 갭이 과도하면 취소

단, 이 전략은 `paper_only_candidate`가 되기 전까지 후보 설명용/관찰용으로 둔다.

## Data Contract

모든 입력 데이터는 다음 필드를 최대한 갖춰야 한다.

```text
source
source_version
collected_at
as_of_time
source_bas_dt
ticker
```

현재 원천 데이터의 한계:

- `data/kr_stock_price_history.csv`: 770,099행, 2,836종목, 2025-03-14~2026-05-07 일봉 데이터
- `data/eod_context/investor_flows.csv`: 일별 투자자별 순매수
- `data/eod_context/short_credit.csv`: 공매도/신용 관련 필드
- `data/eod_context/disclosures.csv`: 공시 이벤트와 risk_flag
- 재무제표 팩터는 현재 로컬 데이터만으로 충분하지 않으므로 MVP에서는 미구현/데이터 없음으로 표시

룩어헤드 방지:

- 기준일 이후 정보는 후보 점수에 절대 사용하지 않는다.
- `source_bas_dt`와 `as_of_time`이 없는 데이터는 추천 엔진 입력에서 제외하거나 낮은 신뢰도로 표시한다.
- 공시/수급은 실제 사용 가능 시각을 기록해야 하며, 시각이 불명확하면 EOD 다음 거래일부터만 사용한다.

## Architecture

```text
data/
  -> v2 data loader
  -> data health checker
  -> feature builder
  -> strategy scorer
  -> validation runner
  -> promotion gate
  -> daily recommendation writer
  -> static web decision desk
```

예상 모듈:

```text
src/kr_daypilot_v2/
  __init__.py
  contracts.py          데이터 스키마와 상태 enum
  data_health.py        신선도, 누락, 커버리지 점검
  features.py           EOD 팩터 생성
  event_gate.py         공시 이벤트 분류와 리스크 차단
  strategies.py         후보 점수화
  validation.py         워크포워드 검증과 promotion state
  recommend.py          일일 후보 산출 CLI
  render.py             HTML/JSON/CSV 출력
tests/
  test_v2_features.py
  test_v2_event_gate.py
  test_v2_validation.py
  test_v2_render.py
output/v2/
  latest.json
  latest.csv
  latest.html
```

## Web Decision Desk

첫 화면은 마케팅 페이지가 아니라 의사결정 화면이다.

상단:

- 오늘 상태: `TRADE_REVIEW_ALLOWED`, `PAPER_ONLY`, `NO_TRADE`, `STALE_DATA`, `RISK_DAY`
- 데이터 기준일
- 후보 수
- 차단 후보 수
- 마지막 검증 상태

탭:

- Today: 오늘 후보 테이블
- Evidence: 전략군별 검증 성과
- Data Health: 데이터 신선도와 누락
- Strategy Lab: 전략별 점수 구성
- Decision Log: 매일 산출 결과와 차단 사유

후보 테이블 필드:

```text
rank
ticker
company
market
strategy_family
state
score
entry_zone
target
stop
hold_days
factor_summary
event_gate
data_confidence
evidence_summary
block_reason
```

UI 원칙:

- `매수 추천`이라는 문구를 쓰지 않는다.
- 검증 미통과 전략은 눈에 띄게 `research_only`로 표시한다.
- 종목명보다 상태, 근거, 차단 사유가 먼저 읽히게 한다.
- 후보가 없을 때는 정상 상태로 처리하고, "오늘은 쉬는 날"을 명확히 보여준다.

## Commands

현재 확인용 명령:

```powershell
python -m kr_precision_backtest.run_research_gate1 --max-reference-days 250 --max-candidates 5 --hold-days 5
python -m compileall src
python scripts/verify_dashboard_render.py
```

v2 목표 명령:

```powershell
python -m kr_daypilot_v2.recommend --as-of latest --output output/v2
python -m kr_daypilot_v2.validation --windows 20 --hold-days 5 --output output/v2_validation
python -m kr_daypilot_v2.render --input output/v2/latest.json --output output/v2/latest.html
python -m unittest discover -s tests
```

## Code Style

핵심 로직은 pandas 기반의 명시적 함수로 작성한다.

```python
def build_candidate_state(row: pd.Series, evidence: StrategyEvidence) -> CandidateState:
    if row["data_stale"]:
        return CandidateState("blocked", "stale_data")
    if row["event_gate"] == "risk_block":
        return CandidateState("blocked", "event_risk")
    if evidence.promotion_state != "paper_only_candidate":
        return CandidateState("research_only", evidence.reason)
    return CandidateState("paper_only_candidate", "")
```

규칙:

- 점수 계산과 차단 사유 계산을 분리한다.
- `score`가 높아도 `blocked`면 화면 추천 영역에 올리지 않는다.
- 데이터가 없을 때 0점으로 조용히 대체하지 말고 `data_unavailable` 또는 `low_confidence`로 표시한다.
- HTML 렌더링은 계산 로직을 포함하지 않는다.

## Testing Strategy

단위 테스트:

- 피처 생성이 기준일 이후 데이터를 쓰지 않는지 검증
- 이벤트 게이트가 위험 공시를 차단하는지 검증
- 데이터 누락 시 조용히 통과하지 않고 상태가 내려가는지 검증
- 추천 상태가 promotion gate를 우회하지 않는지 검증

통합 테스트:

- 작은 CSV fixture로 `recommend -> latest.json/latest.csv/latest.html` 생성
- 후보가 0개여도 HTML이 정상 렌더링되는지 확인
- `latest.json`의 후보 상태와 HTML 표시 상태가 일치하는지 확인

브라우저 검증:

- `output/v2/latest.html`을 열어 핵심 문구와 테이블 렌더링 확인
- 후보 없음, stale data, risk_block 상태를 각각 스냅샷으로 확인

## Boundaries

Always:

- 기존 실패 검증 결과를 보존한다.
- 추천 상태와 검증 상태를 분리한다.
- 데이터 기준일과 신뢰도를 화면에 표시한다.
- 비밀값과 API 키를 출력하지 않는다.
- 실전 주문 기능은 기본 비활성화한다.

Ask first:

- 유료 데이터 추가
- 새 API 키/계좌 권한 사용
- 실전 주문 또는 모의투자 주문 연동
- 기존 v1 코드 삭제 또는 대규모 리팩터링
- 글로벌 Codex 설정 변경

Never:

- 검증 실패 전략을 매수 추천으로 표시
- 장중 단타/VI/상한가 추격을 자동주문으로 구현
- `.env` 내용 출력
- 기준일 이후 데이터를 피처에 사용
- 사용자 승인 없이 전역 설정 파일 수정

## Success Criteria

1. `python -m kr_daypilot_v2.recommend --as-of latest --output output/v2`가 로컬 데이터만으로 실행된다.
2. 출력 파일 `output/v2/latest.json`, `output/v2/latest.csv`, `output/v2/latest.html`이 생성된다.
3. 모든 후보는 `state`, `factor_summary`, `event_gate`, `data_confidence`, `evidence_summary`, `block_reason`을 가진다.
4. 검증 미통과 전략 후보는 `paper_only_candidate`로 승격되지 않는다.
5. 기준일 이후 데이터 누수 테스트가 통과한다.
6. 브라우저 렌더 검증이 통과한다.
7. 실전 주문 또는 매수 실행 기능은 포함되지 않는다.

## Implementation Tasks

- [ ] Task 1: v2 데이터 계약과 상태 enum 정의
  - Acceptance: 후보, 데이터 상태, 전략 증거 구조가 타입으로 고정됨
  - Verify: `python -m unittest tests.test_v2_contracts`
  - Files: `src/kr_daypilot_v2/contracts.py`, `tests/test_v2_contracts.py`

- [ ] Task 2: 데이터 헬스 체크 구현
  - Acceptance: 일봉, 유니버스, 수급, 공시의 기준일/누락/커버리지를 JSON으로 출력
  - Verify: `python -m unittest tests.test_v2_data_health`
  - Files: `src/kr_daypilot_v2/data_health.py`, `tests/test_v2_data_health.py`

- [ ] Task 3: EOD 멀티팩터 라이트 피처 생성
  - Acceptance: 가격/거래대금/모멘텀/변동성/수급/공시 리스크 피처 생성
  - Verify: 룩어헤드 방지 fixture 테스트
  - Files: `src/kr_daypilot_v2/features.py`, `tests/test_v2_features.py`

- [ ] Task 4: 이벤트 게이트 구현
  - Acceptance: risk_block, positive_watch, neutral, needs_manual_review 분류
  - Verify: 공시 제목 fixture 테스트
  - Files: `src/kr_daypilot_v2/event_gate.py`, `tests/test_v2_event_gate.py`

- [ ] Task 5: 후보 스코어링과 promotion gate 연결
  - Acceptance: 점수와 상태가 분리되고, evidence 미통과 후보는 research_only로 남음
  - Verify: `python -m unittest tests.test_v2_strategies`
  - Files: `src/kr_daypilot_v2/strategies.py`, `tests/test_v2_strategies.py`

- [ ] Task 6: 일일 추천 CLI 구현
  - Acceptance: latest JSON/CSV 생성
  - Verify: `python -m kr_daypilot_v2.recommend --as-of latest --output output/v2`
  - Files: `src/kr_daypilot_v2/recommend.py`

- [ ] Task 7: 정적 HTML Decision Desk 구현
  - Acceptance: Today, Evidence, Data Health, Decision Log 화면 렌더링
  - Verify: 브라우저 렌더 검증
  - Files: `src/kr_daypilot_v2/render.py`, `output/v2/latest.html`

- [ ] Task 8: 기존 RG1 결과와 v2 후보를 함께 보여주는 검증 섹션 연결
  - Acceptance: 전략군별 최근 validation 상태가 화면에 표시됨
  - Verify: latest.html에서 `research_gate_pass=false`와 차단 상태 확인
  - Files: `src/kr_daypilot_v2/validation.py`, `src/kr_daypilot_v2/render.py`

## Open Questions

1. 첫 v2는 정적 HTML로 충분한가, 아니면 FastAPI/Streamlit 같은 로컬 서버형 웹앱이 필요한가?
2. 유료 재무 데이터 없이 "멀티팩터 라이트"로 시작해도 되는가?
3. 추천 결과의 기본 보유기간은 1~5거래일로 유지할 것인가?
4. 실전 주문은 계속 금지하고, 모의투자/페이퍼 플랜까지만 허용할 것인가?

## Source Reports

- Local ChatGPT report: `deep-research-report.md`
- Local Gemini report PDF: `국내 주식 투자 기법 검증 및 자동화 전략 연구.pdf`
- ChatGPT share: https://chatgpt.com/share/6a0a9300-3f2c-83a6-9e7b-9a635ed682d6
- Gemini share: https://gemini.google.com/share/503c9d4f8e2a
