# User Next Steps

## What You Should Decide Now

These choices define the research and later paper/live promotion thresholds.

### 1. Data Access Policy

Choose how aggressively the project may expand data sources.

Recommended default:

```text
Use free/current APIs first.
Add paid or contract data only when a strategy cannot be fairly tested without it.
Do not use legally unclear or non-reproducible data.
```

Decision needed:

```text
paid_data_budget: none / low / flexible
allowed_paid_sources: KRX/Koscom / FnGuide / news vendor / other
```

### 2. Trading Horizon

Recommended default:

```text
1-5 trading days
```

Reason:

- Short enough for active systematic trading.
- Long enough to reduce pure intraday noise and execution burden.

### 3. Risk Limits

Recommended default:

```text
per_trade_max_loss: 0.5% of capital
daily_max_loss: 1.0% of capital
weekly_max_loss: 3.0% of capital
pause_after_consecutive_losses: 3
```

### 4. Automation Boundary

Recommended default:

```text
research: fully automated
paper trading: automated
live order: user approval required
fully automatic live trading: disabled until later explicit approval
```

## What the System Will Show You After Research Gate 1

The expected answer will look like this:

```text
Strategy: sector_relative_oversold_flow_reversal
Status: paper_only
Evidence:
  OOS trades: 142
  Target hit: 56.3%
  Stop hit: 31.7%
  Average net return: +0.72%
  Worst consecutive losses: 4
  Works in: neutral/rebound markets
  Fails in: broad weak markets
Decision:
  Paper trade only. Not live yet.
```

Or:

```text
Strategy: current_swing_baseline
Status: retired
Reason:
  Rank ordering is unstable.
  OOS expectancy is negative after costs.
  Stop rate is too high.
```

## What Codex Should Build Next

Immediate next build step:

```text
Build standardized Research Gate 1 runner.
```

The runner should:

1. Load strategy hypotheses from `experiments/strategy_hypotheses.json`.
2. Generate comparable candidate sets per strategy family.
3. Apply the same label and cost model.
4. Run chronological walk-forward validation.
5. Write one experiment folder per run.
6. Update `experiments/registry.csv`.
7. Produce a browser-readable HTML report.

## Do Not Build Yet

Do not prioritize:

- final live-trading automation
- polished web dashboard
- ML model tuning
- new manual filters based on the latest failed backtest

These are later steps. They are only useful after at least one strategy family
shows a repeatable edge.

## Your Practical Checklist

Before live use, prepare answers for:

```text
1. How much capital will the system simulate?
2. What is the maximum loss you can accept per trade?
3. Are paid data sources allowed if needed?
4. Are live orders always approval-only?
5. Is the primary target 1-5 day swing, or should longer holding periods be allowed?
```

If you do not choose values now, the project uses the recommended defaults above.

