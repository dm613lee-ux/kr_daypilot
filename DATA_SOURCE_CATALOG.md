# Data Source Catalog

## Principle

KR DayPilot must be designed for expandable data access. The system must not
assume that only the currently connected APIs are available.

Any source can be added if it can be converted into a normalized, point-in-time
dataset with source metadata.

## Adapter Contract

Every source connector should declare:

```text
source_id
provider
license_status
latency
asset_scope
coverage_start
coverage_end
raw_path
normalized_path
as_of_time_field
point_in_time_safe
quality_checks
allowed_use
```

Every normalized row should include:

```text
source
source_version
collected_at
as_of_time
source_bas_dt
ticker or instrument_id
```

`as_of_time` is mandatory for preventing lookahead bias.

## Current Source Classes

### KRX Open API

Use:

- official daily market data
- listed issue information
- market statistics available through approved endpoints

Role:

- EOD market data
- baseline official data
- reference data

### KRX Data Marketplace / pykrx Session

Use:

- investor trading value
- short-selling transaction data
- short balance data where available

Role:

- EOD supply-demand and short pressure features

Important:

- `KRX_ID` and `KRX_PW` are local secrets and must never be printed.
- If this source becomes unreliable, a paid KRX/Koscom feed should map into the
  same normalized schema.

### KIS Open API

Use:

- current price
- orderbook
- trade strength
- WebSocket real-time quote context
- paper or live order status later, if enabled by the user

Role:

- execution gate
- intraday context
- paper/live fill realism

### OpenDART

Use:

- official disclosure list
- filing titles and event metadata
- risk-event classification

Role:

- hard risk gate
- event-driven research

## Expandable Sources

### Paid KRX / Koscom Feed

Potential use:

- deeper historical tick or orderbook data
- official delayed or real-time feeds
- historical investor-type or short-selling datasets

Expected mapping:

```text
normalized/trade_ticks/
normalized/orderbook_snapshots/
normalized/investor_flows/
normalized/short_sale/
```

### Fundamental and Consensus Vendors

Examples:

- FnGuide or equivalent paid vendor
- analyst consensus
- earnings estimate revisions
- valuation and quality data

Expected features:

```text
quality_score
earnings_revision
value_score
profitability_score
leverage_risk
```

### News and Text Data

Potential use:

- event detection
- sentiment classification
- rumor/risk filtering

Rule:

- News should not be used as an automatic alpha source until historical
  timestamped replay is available.
- It can be used earlier as a risk-review aid.

### Macro and Cross-Asset Data

Potential use:

- USD/KRW
- interest rates
- KOSPI futures
- foreign futures flow
- global index overnight moves
- sector ETF proxies

Role:

- market-regime model
- risk-on/risk-off classification

### Alternative Data

Potential use:

- search trends
- app usage
- traffic
- supply-chain signals

Rule:

- Only use if legal, timestamped, reproducible, and normalized.
- No private or unauthorized data.

## Source Tiers

```text
Tier 0: already connected and working
Tier 1: accessible with existing account/API approval
Tier 2: paid vendor or Koscom/KRX contract
Tier 3: experimental or alternative data
```

The strategy system should record which tier each feature depends on. A strategy
that requires Tier 2 data must not silently appear as available when only Tier 0
data exists.

## Quality Checks

Each source must pass:

- freshness check
- duplicate check
- missing ticker/date check
- schema check
- timestamp availability check
- outlier check
- survivorship-bias note if universe is reconstructed

## Official Reference Links

- KRX Data Marketplace: https://data.krx.co.kr/
- KRX Open API: https://openapi.krx.co.kr/
- KIS Open API sample repository: https://github.com/koreainvestment/open-trading-api
- OpenDART API: https://opendart.fss.or.kr/

