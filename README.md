# KR DayPilot

`KR DayPilot`은 기존 국내주식 앱과 분리된 별도 프로젝트입니다.

목표는 직장인이 장중 계속 매매 화면을 보지 않아도 사용할 수 있는 단기 국내주식 추천·조건부 주문 앱을 단계적으로 검증하는 것입니다.

## 프로젝트 경계

- 기존 국내주식 앱 폴더의 코드는 참고할 수 있습니다.
- `KR DayPilot` 실행은 기존 국내주식 앱 폴더의 파일을 직접 읽지 않습니다.
- 가격 데이터는 `data/` 폴더 안의 독립 복사본을 사용합니다.
- API 키는 `KR DayPilot/.env`에 별도 복사본으로 보관합니다.
- 기존 국내주식 앱을 변경하거나 삭제해도 `KR DayPilot`이 깨지지 않는 구조를 목표로 합니다.

## 현재 단계

Phase 1은 KRX 일봉 데이터 기반 proxy 백테스트입니다.

현재 구현은 아래 파일을 읽습니다.

```text
data/kr_stock_price_history.csv
config/policy.defaults.json
```

결과는 아래 폴더에 생성됩니다.

```text
output/
```

## 실행

처음 실행 전, 현재 PC의 기본 Python에 pandas가 없으면 먼저 실행합니다.

```text
환경_설치.bat
```

검증 실행:

```text
검증_실행.bat
```

결과 확인:

```text
결과_열기.bat
```

## Phase 1B 분봉 검증

KIS 당일 1분봉 데이터를 수집합니다.

```text
분봉수집_실행.bat
```

수집된 분봉으로 `09:30~10:30` 구간의 VWAP 재탈환 전략을 검증합니다.

```text
분봉검증_실행.bat
```

분봉 검증 결과를 엽니다.

```text
분봉결과_열기.bat
```

## Phase 1C 일일 누적 파이프라인

아래 실행 파일은 `일봉 proxy 검증 -> 최신 기준일 후보 추출 -> KIS 분봉 수집 -> 분봉 검증 -> 일일 리포트 생성`을 한 번에 수행합니다.

```text
일일수집검증_실행.bat
```

결과 확인:

```text
일일결과_열기.bat
```

매일 장 종료 후 자동으로 누적 수집하려면 Windows 예약 작업 설치 파일을 실행합니다.

```text
예약작업_설치.bat
```

예약 작업을 제거하려면:

```text
예약작업_삭제.bat
```

예약 기본 시각은 매일 `15:45`입니다. KIS 당일분봉 API가 당일 데이터만 제공하므로, 장 종료 후 같은 날 실행해야 데이터가 누적됩니다.

주의:

- KIS 공식 샘플 기준 `주식당일분봉조회`는 당일 분봉만 제공합니다.
- 과거 여러 날 검증은 이 배치 파일을 장중/장후에 반복 실행해 `data/intraday/minute_bars/`에 데이터를 누적해야 가능합니다.
- `.env`의 API 키 값은 출력하지 않습니다.

## 기본 검증 정책

- 목표수익률: +1.8%
- 손절률: -0.9%
- 백테스트 비용: 왕복 0.60%
- 하루 최대 후보: 2개
- 20일 평균 거래대금: 100억 원 이상
- 일봉 proxy 기준: close location, 거래대금 증가, 하단 회복, 시장 레짐

## 중요한 한계

이 Phase 1은 일봉 OHLCV 기반 proxy입니다. 아직 아래 정보는 실제로 재현하지 않습니다.

- 1분봉 VWAP
- 09:30 기준 거래대금
- 실시간 호가 스프레드
- 장중 뉴스/공시
- 실제 KIS 주문 체결/부분체결

따라서 이 결과는 실전 매매 가능성을 판단하는 1차 검증이지, 자동 주문 허가 기준이 아닙니다.

## Phase 2A/2B 누적 성과 대시보드

일일 파이프라인을 실행하면 종목·날짜별 검증 결과가 아래 파일에 누적됩니다.

```text
data/performance/trade_log.csv
```

누적 성과는 아래 HTML에서 확인합니다.

```text
output/dashboard/latest.html
```

초보자용 실행 파일:

```text
성과대시보드_열기.bat
```

대시보드는 `자동 파이프라인 추천 후보`만 기본 성과에 포함합니다. 수동으로 수집한 분봉 파일은 별도 행으로 보관되지만 추천 성공률 계산에는 포함하지 않습니다.

브라우저 렌더링 검증:

```text
브라우저검증_실행.bat
```

이 검증은 기본 Python에 설치된 Playwright와 Playwright Chromium을 사용합니다. 대시보드 HTML 렌더링, 핵심 문구, 카드/표 수, 인코딩, 스크린샷 생성을 확인합니다.

## Swing Paper Plan

새 전략 레이어는 실전 주문이 아니라 `1~3일 스윙 + 페이퍼 우선` 검증용입니다.

스윙 백테스트:

```text
스윙검증_실행.bat
스윙검증결과_열기.bat
```

오늘의 페이퍼 주문 플랜:

```text
페이퍼플랜_실행.bat
페이퍼플랜_열기.bat
```

출력 위치:

```text
output/swing_backtest/latest.html
output/app/latest.html
```

판정 기준은 목표가 도달률만 보지 않고 체결률, 손절률, 시간청산, 비용 차감 순수익률, Wilson 하한을 함께 봅니다. 현재 단계는 연구/페이퍼 검증이며 자동 실전 주문은 포함하지 않습니다.

## EOD Feature Stop Analysis

다음 단계는 새 스윙 백테스트 결과 위에 EOD 수급, 공시, 시장국면, 섹터 컨텍스트를 붙이고 어떤 feature가 손절률을 실제로 낮추는지 검증하는 단계입니다.

현재 구현은 안전하게 observe-only입니다. 즉, 수급/공시/섹터 feature를 바로 매수 추천 차단 규칙으로 쓰지 않고, 백테스트 결과 CSV에 붙인 뒤 feature 구간별 손절률·목표도달률·표본 수를 비교합니다.

입력 스키마:

```text
EOD_CONTEXT_SCHEMA.md
data/eod_context/investor_flows.csv
data/eod_context/short_credit.csv
data/eod_context/disclosures.csv
```

분석 실행:

```text
KRX접근진단_실행.bat
KRX접근진단결과_열기.bat
EOD컨텍스트_수집.bat
EOD컨텍스트_결과_열기.bat
피처손절분석_실행.bat
피처손절분석결과_열기.bat
```

출력 위치:

```text
output/feature_validation/latest.html
output/feature_validation/latest.csv
```

손절률을 낮추는 후보 feature가 보여도 표본 300건 이상과 시간순 holdout 검증 전에는 자동 차단/가점 규칙으로 승격하지 않습니다.

KRX 수급/공매도 수집은 두 접근권한을 구분해서 봅니다.

- `KRX_API_KEY`: KRX Open API 일별매매정보 등 승인된 Open API 호출용입니다.
- `KRX_ID`, `KRX_PW`: pykrx가 KRX Data Marketplace 세션을 만들 때 필요한 로그인 정보입니다. 종목별 투자자 수급/공매도 데이터는 이 세션이 없으면 수집되지 않습니다.

`.env`에 로그인 정보를 추가한 뒤 `KRX접근진단_실행.bat`을 먼저 실행해 접근 가능 여부를 확인합니다. 키 값은 리포트에 출력하지 않습니다.
