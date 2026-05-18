# KR DayPilot System Investing Architecture

## Purpose

KR DayPilot is being repositioned from a single stock recommendation app into a
systematic investing research and operating platform.

The system should not answer only:

```text
Which stock should I buy today?
```

It should answer:

```text
Which investment logic has been verified, under which market regime, with what
expected return, drawdown, execution risk, and data confidence?
```

This distinction is important. A recommendation without a verified strategy
history is not a system. It is an opinion.

## What the System Should Confirm

The system must confirm the following before any strategy is treated as a live
candidate.

1. Whether a repeatable edge exists
   - The strategy must beat a simple benchmark after costs.
   - It must work in out-of-sample periods, not only on the full backtest.

2. Which market regime supports the edge
   - Bull, bear, rebound, range, high-volatility, low-liquidity, sector-led.
   - A strategy that works only in one hidden regime must be labeled as such.

3. Whether the signal is tradable
   - Entry price can realistically fill.
   - Exit and stop can realistically execute.
   - Expected edge survives spread, fees, tax, and slippage.

4. Whether the risk is acceptable
   - Maximum adverse excursion.
   - Consecutive losses.
   - Daily and weekly loss limits.
   - Sector concentration and event risk.

5. Whether today's signal is promoted or blocked
   - `research_only`: idea exists but not verified.
   - `paper_only`: verified enough to paper trade.
   - `live_candidate`: paper evidence is strong enough for limited live review.
   - `blocked`: data stale, market regime mismatch, risk gate fail, or strategy kill rule.

## Architecture

```text
Data Lake
  -> Feature Store
  -> Strategy Hypothesis Registry
  -> Experiment Runner
  -> Walk-forward Validator
  -> Promotion Gate
  -> Paper Trading Ledger
  -> Live Candidate Engine
  -> Risk Manager
  -> Web Decision Desk
```

## Data Lake

Raw data must be retained separately from normalized research data.

```text
data_lake/
  raw/
    krx/
    kis/
    dart/
    news/
    macro/
    vendor/
  normalized/
    daily_ohlcv/
    minute_bars/
    investor_flows/
    short_sale/
    credit_balance/
    disclosures/
    orderbook_snapshots/
    trade_ticks/
    market_index/
    sector_index/
  snapshots/
    asof_YYYYMMDD_HHMM/
```

Rules:

- Raw data is append-only.
- Normalized data is versioned.
- Every row must carry source and time availability metadata.
- A feature may only use information that was available at the decision time.
- Adding a new API should not require changing strategy code directly. It should
  add a connector and normalized dataset.

## Feature Store

Feature groups:

```text
price_action:
  ret_1d, ret_3d, ret_20d, gap, range, close_location, volatility

liquidity:
  trading_value, turnover, spread_proxy, volume_shock

supply_demand:
  foreign_net_buy, institution_net_buy, retail_net_buy, flow_reversal

short_credit:
  short_sale_ratio, short_balance_ratio, credit_balance_ratio

event_risk:
  financing_risk, audit_risk, trading_halt, litigation, major_holder_change

regime:
  market_trend, market_breadth, volatility_regime, sector_strength

relative_value:
  sector_relative_return, residual_zscore, pair_spread_zscore

execution:
  orderbook_imbalance, trade_strength, vwap_distance, opening_range
```

## Strategy Families

Research Gate 1 starts with five independent strategy families.

1. Sector relative oversold plus flow reversal
2. Momentum pullback in strong regimes
3. Event overreaction with hard risk gate
4. Sector or pair residual mean reversion
5. Order-flow execution timing

The current rapid-reversal strategy is retained only as a baseline. It should not
be treated as the main alpha.

## Validation Standard

Each strategy must be tested by:

- In-sample exploration
- Walk-forward out-of-sample validation
- Cost and slippage sensitivity
- Market-regime breakdown
- Liquidity breakdown
- Paper-trading replay
- Promotion review

Minimum promotion logic:

```text
research_only -> paper_only:
  - out-of-sample net expectancy > 0
  - enough trades to avoid anecdotal evidence
  - no single regime explains all performance
  - stop-loss rate or drawdown is within policy

paper_only -> live_candidate:
  - paper fills are realistic
  - at least 100 paper-filled trades or a Director-approved exception
  - paper result remains positive after all costs
  - consecutive loss and drawdown limits are acceptable
```

## Risk Policy Defaults

These are research defaults, not investment advice.

```text
per_trade_max_loss: 0.5% of capital
daily_max_loss: 1.0% of capital
weekly_max_loss: 3.0% of capital
max_consecutive_losses_before_pause: 3
default_holding_period: 1-5 trading days
live_before_paper: forbidden
```

## Web Decision Desk

The app should eventually show:

1. System status
   - trade allowed, paper only, no trade, stale data, risk day

2. Active strategies
   - which strategies are enabled today and why

3. Candidate plans
   - ticker, strategy, entry, target, stop, size, max loss, cancel condition

4. Evidence
   - historical OOS performance for the exact strategy family and regime

5. Risk panel
   - daily loss budget, open exposure, sector concentration, consecutive losses

6. Data health
   - KRX, KIS, DART, vendor feeds, freshness, missing fields

## Sources and Expandability

Current and planned source classes include:

- KRX Data Marketplace and KRX Open API for market data and official statistics.
- KIS Open API for quotes, orderbook, trade strength, paper/live execution context.
- OpenDART for official disclosure events.
- Koscom or paid KRX feeds if deeper historical market data is required.
- Additional vendor data such as fundamentals, consensus, news, macro, sector,
  index, or alternative data when legally available.

The system must not assume the current API set is complete.

