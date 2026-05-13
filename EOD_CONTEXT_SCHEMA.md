# EOD Context Data Schema

이 문서는 `KR DayPilot` 스윙 백테스트에 장마감 이후 컨텍스트 데이터를 붙이기 위한 선택 입력 형식입니다.

현재 구현은 이 파일들이 없어도 작동합니다. 파일이 있으면 `source_bas_dt + ticker` 기준으로 left join하고, 없거나 누락된 값은 `*_available=false` 또는 0으로 처리합니다. 이 단계의 목적은 자동 차단이 아니라 feature별 손절률 감소 여부를 검증하는 것입니다.

## 위치

```text
data/eod_context/
```

## investor_flows.csv

투자자별 EOD 순매수 데이터입니다.

```csv
source_bas_dt,ticker,foreign_net_buy_value,institution_net_buy_value,retail_net_buy_value,source,updated_at
20260507,005930,0,0,0,krx,2026-05-08T18:00:00+09:00
```

필수 컬럼:

- `source_bas_dt` 또는 `date`
- `ticker`

선택 컬럼:

- `foreign_net_buy_value`
- `institution_net_buy_value`
- `retail_net_buy_value`

## short_credit.csv

공매도/신용잔고 리스크 데이터입니다.

```csv
source_bas_dt,ticker,short_sale_value_ratio,credit_balance_ratio,source,updated_at
20260507,005930,0.0,0.0,krx,2026-05-08T18:00:00+09:00
```

필수 컬럼:

- `source_bas_dt` 또는 `date`
- `ticker`

선택 컬럼:

- `short_sale_value_ratio`
- `credit_balance_ratio`

## disclosures.csv

OpenDART 공시 이벤트 데이터입니다.

```csv
source_bas_dt,ticker,corp_code,receipt_no,receipt_dt,title,event_type,risk_flag,source_url,source,updated_at
20260507,005930,00126380,20260507000000,2026-05-07T15:30:00+09:00,단일판매공급계약,contract,false,https://example.com,opendart,2026-05-07T18:00:00+09:00
```

필수 컬럼:

- `source_bas_dt` 또는 `date`
- `ticker`

선택 컬럼:

- `event_type`
- `title`
- `risk_flag`

주의:

- 공시는 시점이 중요합니다. 장마감 후 공시는 다음 거래일 판단에는 사용할 수 있지만, 같은 날 장중 판단에 섞으면 lookahead가 됩니다.
- `risk_flag=true`는 현재 분석 리포트의 관측 feature입니다. 자동 제외 규칙으로 승격하려면 별도 holdout 검증이 필요합니다.

## 현재 파생 feature

별도 파일 없이 가격 히스토리에서 계산합니다.

- `market_regime`
- `market_advancing_ratio`
- `market_ret_1d_median_pct`
- `sector_group`
- `sector_source`
- `sector_relative_strength_5d_pct`

현재 `kr_universe.csv`의 `sector` 값은 대부분 `KOSPI/KOSDAQ` 수준이라 세부 업종이 아니라 시장 프록시로 취급합니다.
