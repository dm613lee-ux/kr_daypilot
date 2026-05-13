from __future__ import annotations

import argparse
from pathlib import Path

from .data import add_daily_proxy_features, load_price_history
from .policy import load_policy
from .report import write_outputs
from .simulator import BacktestOptions, run_backtest


PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1 KR intraday precision proxy backtest.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-reference-days", type=int, default=250)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    history = add_daily_proxy_features(load_price_history(args.price_history))
    results, summary = run_backtest(history, policy, BacktestOptions(max_reference_days=max(args.max_reference_days, 1)))
    paths = write_outputs(results, summary, policy, args.output)

    print("Phase 1 backtest complete.")
    print(f"Reference days: {summary.get('reference_start', '')} ~ {summary.get('reference_end', '')}")
    print(f"Recommendations: {summary.get('recommendations', 0)}")
    print(f"Entries: {summary.get('entries', 0)}")
    print(f"Successes: {summary.get('successes', 0)}")
    print(f"Failures: {summary.get('failures', 0)}")
    print(f"Success rate: {summary.get('success_rate', 0)}%")
    print(f"Wilson 95% lower: {summary.get('wilson_low', 0)}%")
    print(f"Average net return: {summary.get('avg_net_return_pct', 0)}%")
    print(f"Research pass: {summary.get('research_pass', False)}")
    print(f"HTML: {paths['html']}")
    print(f"CSV: {paths['csv']}")
    print(f"JSON: {paths['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
