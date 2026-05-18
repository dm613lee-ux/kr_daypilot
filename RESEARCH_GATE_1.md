# Research Gate 1

## Goal

Research Gate 1 answers one question:

```text
Do we have at least one repeatable, tradable investment logic that remains
positive after costs in out-of-sample validation?
```

This is not an MVP. A normal MVP proves that a product can be used. Research Gate
1 proves whether the investing logic is worth turning into a product.

## Why This Gate Exists

The current swing strategy repeatedly produced `research_pass=false`.

Important finding:

- The problem is not only insufficient data.
- The current alpha ranking is weak.
- Rank 1 performed worse than rank 2.
- Adding filters after seeing failures risks overfitting.

Therefore, the next step is not to tune the current score. The next step is to
compare independent strategy families under the same validation rules.

## Strategy Families to Test

### RG1-001: Sector Relative Oversold plus Flow Reversal

Hypothesis:

```text
A stock that is oversold versus its sector, has no hard event risk, and shows
foreign/institution flow stabilization has a higher D+1 to D+5 rebound expectancy.
```

Core features:

- sector relative return
- 1d/3d/5d oversold level
- foreign and institution net-buy z-score
- retail exhaustion
- disclosure risk flag
- liquidity and spread proxy

### RG1-002: Momentum Pullback in Strong Regime

Hypothesis:

```text
In a strong market or strong sector regime, medium-term winners that pull back on
reduced volume recover better than generic crash candidates.
```

Core features:

- 20d/60d momentum
- 3d pullback
- volume contraction
- sector strength
- market breadth
- distance from recent high

### RG1-003: Event Overreaction with Hard Risk Gate

Hypothesis:

```text
Some disclosure or news events create temporary overreaction, but financing,
audit, litigation, trading-halt, and governance events should be blocked rather
than traded.
```

Core features:

- disclosure event type
- event severity
- abnormal return after event
- abnormal volume
- next-session stabilization

### RG1-004: Sector or Pair Residual Mean Reversion

Hypothesis:

```text
After removing market and sector movement, abnormal residual weakness in liquid
stocks partially mean-reverts.
```

Core features:

- sector beta
- residual return z-score
- pair or peer group spread
- liquidity
- regime filter

### RG1-005: Order-flow Execution Timing

Hypothesis:

```text
Orderbook imbalance, trade strength, and VWAP relationship do not create the
core alpha alone, but they improve entry timing and reduce stop-outs.
```

Core features:

- orderbook imbalance
- trade strength
- VWAP distance
- opening range
- spread and depth
- fill probability

## Labels

Every strategy must produce the same label set.

```text
paper_filled
target_hit_d1_d5
stop_hit_d1_d5
time_exit
max_adverse_excursion_pct
max_favorable_excursion_pct
net_return_after_cost_pct
fill_quality
missed_trade_outcome
```

## Baselines

Every strategy must beat simple alternatives.

- Buy KOSPI/KOSDAQ ETF proxy
- Simple short-term reversal
- Simple 20d/60d momentum
- Random liquid-stock basket with same holding period
- Current KR DayPilot swing baseline

## Validation Rules

Use chronological validation only.

```text
Train window -> validation window -> holdout window
roll forward
repeat
```

Do not optimize on the full sample and then call it validated.

Required breakdowns:

- KOSPI vs KOSDAQ
- market regime
- sector
- liquidity bucket
- market cap bucket
- disclosure risk
- flow regime
- short-sale pressure

## Promotion Rules

Research strategy can move to `paper_only` only if:

- Out-of-sample expectancy is positive.
- Result survives reasonable fee/slippage assumptions.
- Stop-loss rate is controlled or profit/loss ratio compensates for it.
- The result is not driven by one isolated month or one sector.
- Trade count is sufficient for the claim being made.

Strategy must be killed or paused if:

- OOS expectancy is negative.
- Stop-loss rate is structurally high.
- Performance disappears after costs.
- Performance exists only after data leakage-prone features.
- It only works in one narrow sample discovered after repeated tuning.

## User Decisions Needed Before Live Use

These decisions are not needed to run research, but are needed before paper/live
promotion thresholds become final.

```text
1. Trading capital to simulate
2. Maximum loss per trade
3. Maximum daily loss
4. Maximum weekly loss
5. Preferred holding period
6. Whether live auto-order is forbidden, approval-only, or allowed later
7. Paid data budget and acceptable vendors
```

Recommended starting defaults:

```text
per_trade_max_loss: 0.5% of capital
daily_max_loss: 1.0% of capital
weekly_max_loss: 3.0% of capital
holding_period: 1-5 trading days
live_auto_order: forbidden until paper evidence is strong
```

## Immediate Next Build Step

Build the experiment registry and the first standardized runner so that each
strategy family is measured under the same labels, cost model, and promotion
rules.

Do not rewrite the web app first. The web app should display verified research
outputs, not drive the research.

## Execution Profile Test

Research Gate 1 must evaluate strategy logic separately from order execution
logic.

Default execution profiles:

```text
pullback_limit:
  entry -0.5%, target +3.0%, stop -2.0%

close_confirm:
  entry 0.0%, target +2.5%, stop -1.8%

strength_follow:
  entry +0.2%, target +3.0%, stop -2.0%

wide_swing:
  entry -0.5%, target +5.0%, stop -3.0%

tight_risk:
  entry -0.3%, target +2.0%, stop -1.2%, shorter hold
```

Reason:

- If the same strategy fails under every execution profile, the strategy logic is
  likely weak.
- If one execution profile consistently improves many strategies, the old order
  rule was likely part of the problem.
- If only a tiny sample turns positive, it stays `needs_more_sample`.

The current runner reports results at the `strategy_family + execution_profile`
level.
