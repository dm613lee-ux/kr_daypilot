# Research Gate 2

## Goal

Research Gate 2 tests a domestic-stock multi-factor recommendation engine.

It answers:

```text
Do value, profitability, momentum, and low-volatility factors create a
repeatable portfolio edge after costs, slippage, disclosure-risk controls, and
chronological rebalancing?
```

This gate is research and paper-only. It never enables live order automation.

## Strategy Families

RG2 currently compares three fixed profiles:

- `rg2_multifactor_balanced`: equal value, profitability, momentum, low-volatility weights
- `rg2_quality_value_momentum`: quality/value anchored with momentum confirmation
- `rg2_defensive_low_vol`: profitability and low-volatility tilt

The profiles are fixed hypotheses. Do not tune weights from the full backtest and
then call the result validated.

## Data Inputs

Required:

```text
data/kr_stock_price_history.csv
data/eod_context/disclosures.csv
data/eod_context/investor_flows.csv
data/eod_context/short_credit.csv
```

Optional but required for paper promotion:

```text
data/fundamentals/fundamental_snapshots.csv
data/fundamentals/krx_valuation.csv
```

`fundamental_snapshots.csv` is the normalized OpenDART-style input. Supported
columns:

```csv
source_bas_dt,ticker,revenue,operating_income,net_income,equity,total_assets,source,updated_at
20260331,005930,0,0,0,0,0,opendart,2026-05-18T18:00:00+09:00
```

`krx_valuation.csv` is an optional KRX valuation input. Supported columns:

```csv
source_bas_dt,ticker,per,pbr,dividend_yield,source,updated_at
20260331,005930,10.5,1.2,2.1,krx,2026-05-18T18:00:00+09:00
```

The runner forward-fills each ticker only from snapshots whose `source_bas_dt`
is on or before the signal date. This prevents lookahead from later filings.

## Factor Definitions

- Value: earnings yield, book-to-market, sales yield, dividend yield
- Profitability: operating margin, ROE, ROA
- Momentum: 20d, 60d, and 120d price momentum
- Low volatility: inverse 60d and 120d realized volatility

Each factor group is percentile-ranked cross-sectionally on the signal date.
Disclosure risk events are hard-blocked by default. If run with
`--allow-risk-disclosure-penalty-only`, they are penalized instead of blocked.

## Validation

The runner uses end-of-period signals:

- monthly: last trading day of each month
- weekly: last trading day of each week

Entry is next trading day open. Exit is the next signal day close. Returns are
calculated after round-trip cost and slippage.

Promotion can only reach:

```text
paper_only
```

Live automation remains disabled even when a strategy passes.

## Run

Run fundamental collection before RG2 when OpenDART/KRX credentials are
available:

```text
펀더멘털수집_실행.bat
펀더멘털수집결과_열기.bat
```

CLI:

```powershell
python -m kr_precision_backtest.collect_fundamentals
```

For a small smoke test:

```powershell
python -m kr_precision_backtest.collect_fundamentals --max-tickers 5 --years 2024 --report-codes 11011
```

One-click:

```text
ResearchGate2_실행.bat
ResearchGate2_결과_열기.bat
```

CLI:

```powershell
python -m kr_precision_backtest.run_research_gate2
```

Useful options:

```powershell
python -m kr_precision_backtest.run_research_gate2 --frequency weekly --portfolio-size 20
python -m kr_precision_backtest.run_research_gate2 --max-periods 24 --slippage-pct 0.2
```

## Sensitivity Validation

After a strategy reaches `paper_only`, run sensitivity validation before using it
for paper review. This repeats RG2 across rebalance frequency, portfolio size,
and slippage assumptions.

One-click:

```text
ResearchGate2_민감도검증_실행.bat
ResearchGate2_민감도검증결과_열기.bat
```

CLI:

```powershell
python -m kr_precision_backtest.run_rg2_sensitivity
```

Useful options:

```powershell
python -m kr_precision_backtest.run_rg2_sensitivity --frequencies monthly,weekly --portfolio-sizes 10,20 --slippage-pcts 0.2,0.5
python -m kr_precision_backtest.run_rg2_sensitivity --min-pass-rate-pct 75
```

Outputs:

```text
output/research_gate2_sensitivity/latest.html
output/research_gate2_sensitivity/latest_strategy_robustness.csv
experiments/registry_rg2_sensitivity.csv
```

## Outputs

Latest report:

```text
output/research_gate2/latest.html
output/research_gate2/latest_summary.json
output/research_gate2/latest_strategy_metrics.csv
```

Durable experiment folder:

```text
experiments/EXP_YYYYMMDD_HHMMSS_RG2/
  config.json
  metrics.json
  portfolio_trades.csv
  portfolio_periods.csv
  strategy_metrics.csv
  walk_forward_windows.csv
  report.html
```

Registry:

```text
experiments/registry_rg2.csv
```

## Current Safety Gate

If OpenDART/KRX fundamental coverage is below the configured threshold, RG2 will
produce rankings for research inspection but will mark strategies as:

```text
needs_fundamental_data
```

If broad KRX valuation data exists but OpenDART profitability data is still too
thin, RG2 will mark strategies as:

```text
needs_profitability_data
```

This is intentional. A price-only proxy must not be promoted as a verified
multi-factor strategy.
