# Spec: Evidence-Based Investment Recommender

## Objective

Build a separate KR DayPilot daily recommendation program that ranks Korean stocks by concrete investment technique evidence, not by the previous Research Gate workflow.

The user should receive 0-20 review candidates with:

- the best matching investment technique,
- score components that explain why the ticker is interesting,
- hard exclusion reasons,
- data quality notes,
- paper-only entry/target/stop planning fields.

This is a recommendation and trading-assistant program. It must not place live orders.

## Assumptions

1. The program runs from local CSV data already under `data/`.
2. The first implementation is batch CLI plus CSV/JSON/HTML output.
3. It is independent from `run_research_gate1.py`, `run_research_gate2.py`, and value-momentum backtest commands.
4. Existing data may include synthetic or future-dated research rows, so the CLI must support `--as-of` and default to the latest date present in the input.
5. Recommendations are not investment advice and remain `paper_review` only.
6. If the latest local price date is too old versus the execution date, paper-review output is blocked as `stale_data` by default.

## Investment Techniques

The system scores named investment techniques. Each technique has a different economic rationale and a different score formula.

### 1. Quality Value Momentum

Rationale: Companies with reasonable valuation, positive profitability, and sustained relative momentum are more robust than cheap-only or momentum-only screens.

Inputs:

- PER, PBR, dividend yield,
- ROE, ROA, operating margin, net margin,
- 120-day and 240-day relative momentum,
- liquidity and event risk.

### 2. Defensive Trend Compounder

Rationale: Low-volatility stocks in an established uptrend can be better candidates for a trading-assistant app than explosive high-volatility names.

Inputs:

- 60-day and 120-day realized volatility,
- trend above moving averages,
- 60-day drawdown,
- profitability and liquidity.

### 3. Flow-Backed Re-Rating

Rationale: Korean stocks often re-rate when price momentum is supported by foreign/institution accumulation rather than only retail-driven spikes.

Inputs:

- 5-day and 20-day foreign/institution net buy pressure,
- relative momentum,
- reasonable valuation,
- liquidity and event risk.

### 4. Event-Safe Recovery

Rationale: Post-decline recovery candidates need clean event risk and improving evidence; adverse DART events should block, not merely discount.

Inputs:

- recent pullback without trend collapse,
- positive profitability or valuation support,
- no risk DART event,
- moderate volatility,
- sufficient liquidity.

## Commands

```powershell
python -m kr_precision_backtest.run_investment_recommender
python -m kr_precision_backtest.run_investment_recommender --as-of 20260518 --top 15
python -m kr_precision_backtest.run_investment_recommender --run-date 20260519 --max-price-age-days 7
python -m kr_precision_backtest.run_investment_recommender --allow-stale-data --output output/investment_recommender_allow_stale_check
python -m unittest tests.test_investment_recommender
```

## Data Freshness Guard

The recommender must not silently present stale local prices as current candidates.

Default rule:

- `run_date` defaults to the current Asia/Seoul date in the CLI.
- `signal_day` is the latest available price date after applying `--as-of`.
- If `run_date - signal_day` is greater than `max_price_age_calendar_days`, the summary state becomes `stale_data`.
- In `stale_data`, all candidates are blocked with `stale_price_data` and the recommendation list is empty.
- `--allow-stale-data` is only for historical/manual inspection. It may emit candidates but marks the state as `paper_review_stale` or `watchlist_stale`.

One-click launcher:

```text
투자근거추천_실행.bat
투자근거추천_결과_열기.bat
```

## Project Structure

```text
src/kr_precision_backtest/investment_recommender.py      scoring logic and data shaping
src/kr_precision_backtest/run_investment_recommender.py  CLI and output writer
tests/test_investment_recommender.py                     unit tests
output/investment_recommender/                           latest CSV/JSON/HTML
docs/INVESTMENT_RECOMMENDER_SPEC.md                      this spec
```

## Code Style

Use small pure functions for scoring and keep I/O in the runner.

```python
def score_quality_value_momentum(frame: pd.DataFrame) -> pd.Series:
    value = mean_score([rank_low(frame["per"]), rank_low(frame["pbr"])], frame.index)
    quality = mean_score([rank_high(frame["roe"]), rank_high(frame["operating_margin"])], frame.index)
    momentum = mean_score([rank_high(frame["relative_momentum_120d_pct"])], frame.index)
    return weighted_score({"value": value, "quality": quality, "momentum": momentum})
```

## Testing Strategy

Use `unittest` with small in-memory pandas frames.

Tests must cover:

- point-in-time feature creation,
- named technique scoring,
- risk disclosure blocking,
- flow-backed technique preference,
- JSON-safe output conversion.

## Boundaries

- Always: keep live order execution disabled, include block reasons, keep output reproducible.
- Ask first: adding new third-party dependencies, changing global Codex config, enabling broker order placement.
- Never: store secrets, print API keys, use future data when an explicit `--as-of` is provided, convert LLM output directly into an order.

## Success Criteria

- `python -m kr_precision_backtest.run_investment_recommender` writes `latest.html`, `latest.csv`, and `latest_summary.json`.
- Recommended rows include `technique`, `final_score`, `score_components`, `evidence_summary`, `paper_plan`, and `state`.
- Unit tests pass.
- The implementation does not import or call Research Gate runners.
