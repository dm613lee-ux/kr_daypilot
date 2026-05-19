from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .fundamental_core_engine import (
    FundamentalCoreConfig,
    build_core_recommendations,
)
from .run_investment_recommender import (
    DEFAULT_DISCLOSURES,
    DEFAULT_FUNDAMENTALS,
    DEFAULT_INVESTOR_FLOWS,
    DEFAULT_PRICE_HISTORY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUATION,
    load_optional_csv,
    load_required_csv,
    write_outputs,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "fundamental_core"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the KR DayPilot Fundamental Core recommendation engine.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--valuation", type=Path, default=DEFAULT_VALUATION)
    parser.add_argument("--investor-flows", type=Path, default=DEFAULT_INVESTOR_FLOWS)
    parser.add_argument("--disclosures", type=Path, default=DEFAULT_DISCLOSURES)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", default="", help="Signal date in YYYYMMDD. Defaults to latest available local price date.")
    parser.add_argument("--run-date", default="", help="Execution date in YYYYMMDD. Defaults to today in Asia/Seoul.")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=62.0)
    parser.add_argument("--min-fundamental-score", type=float, default=45.0)
    parser.add_argument("--min-risk-score", type=float, default=35.0)
    parser.add_argument("--max-core-volatility-60d-pct", type=float, default=8.0)
    parser.add_argument("--min-market-cap-krw", type=float, default=100_000_000_000)
    parser.add_argument("--min-avg-value-20d-krw", type=float, default=5_000_000_000)
    parser.add_argument("--max-price-age-days", type=int, default=7)
    parser.add_argument("--allow-stale-data", action="store_true", help="Allow paper-review output even when local price data is stale.")
    args = parser.parse_args()

    run_date = args.run_date or datetime.now(tz=KST).strftime("%Y%m%d")
    args.run_date = run_date
    config = FundamentalCoreConfig(
        min_market_cap_krw=args.min_market_cap_krw,
        min_avg_value_20d_krw=args.min_avg_value_20d_krw,
        min_score_for_review=args.min_score,
        max_price_age_calendar_days=args.max_price_age_days,
        allow_stale_price_data=args.allow_stale_data,
        min_fundamental_score_for_review=args.min_fundamental_score,
        min_risk_score_for_review=args.min_risk_score,
        max_core_volatility_60d_pct=args.max_core_volatility_60d_pct,
    )
    recommendations, summary = build_core_recommendations(
        load_required_csv(args.price_history),
        fundamentals=load_optional_csv(args.fundamentals),
        valuation=load_optional_csv(args.valuation),
        investor_flows=load_optional_csv(args.investor_flows),
        disclosures=load_optional_csv(args.disclosures),
        universe=load_optional_csv(args.universe),
        config=config,
        as_of=args.as_of or None,
        run_date=run_date,
        top=max(args.top, 1),
    )
    paths = write_outputs(recommendations, summary, args.output, config, args)
    print("KR DayPilot Fundamental Core engine complete.")
    print(f"Signal day: {summary.get('signal_day', '')}")
    print(f"State: {summary.get('state', '')}")
    print(f"Recommendations: {summary.get('recommended', 0)}")
    print(f"Blocked: {summary.get('blocked', 0)}")
    print(f"HTML: {paths['latest_html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
