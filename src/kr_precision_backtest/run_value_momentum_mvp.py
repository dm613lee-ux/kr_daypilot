from __future__ import annotations

import argparse
import csv
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .policy import load_policy, policy_to_dict
from .run_research_gate2 import (
    DEFAULT_EOD_CONTEXT,
    DEFAULT_EXPERIMENTS,
    DEFAULT_FUNDAMENTALS,
    DEFAULT_KRX_VALUATION,
    DEFAULT_POLICY,
    DEFAULT_PRICE_HISTORY,
    DEFAULT_UNIVERSE,
    build_price_lookup,
    build_rebalance_periods,
    display_day,
    max_drawdown,
    position_return_pct,
    prepare_history,
)
from .rg2_factor_engine import build_rebalance_signal_days
from .value_momentum_mvp import (
    ValueMomentumConfig,
    add_value_momentum_features,
    score_value_momentum_candidates,
    select_value_momentum_portfolio,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LATEST_OUTPUT = PROGRAM_ROOT / "output" / "value_momentum_mvp"
DEFAULT_REGISTRY = PROGRAM_ROOT / "experiments" / "registry_value_momentum_mvp.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the report-selected value/quality + momentum MVP strategy.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--eod-context-dir", type=Path, default=DEFAULT_EOD_CONTEXT)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--krx-valuation", type=Path, default=DEFAULT_KRX_VALUATION)
    parser.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    parser.add_argument("--frequency", choices=["monthly", "weekly"], default="monthly")
    parser.add_argument("--portfolio-size", type=int, default=20)
    parser.add_argument("--recommendation-size", type=int, default=10)
    parser.add_argument("--max-periods", type=int, default=36)
    parser.add_argument("--round-trip-cost-pct", type=float, default=-1.0)
    parser.add_argument("--slippage-pct", type=float, default=0.2)
    parser.add_argument("--min-fundamental-coverage-pct", type=float, default=70.0)
    parser.add_argument("--min-periods-for-paper", type=int, default=12)
    parser.add_argument("--min-positions-for-paper", type=int, default=80)
    parser.add_argument("--allow-risk-disclosure-penalty-only", action="store_true")
    args = parser.parse_args()

    policy = load_policy(args.policy)
    cost_pct = policy.backtest_round_trip_cost_default_pct if args.round_trip_cost_pct < 0 else float(args.round_trip_cost_pct)
    config = ValueMomentumConfig(
        min_avg_value_20d_krw=policy.min_avg_trading_value_20d_krw,
        block_risk_disclosures=not args.allow_risk_disclosure_penalty_only,
    )
    history, data_status = prepare_history(
        args.price_history,
        args.eod_context_dir,
        args.universe,
        args.fundamentals,
        args.krx_valuation,
    )
    history = add_value_momentum_features(history)
    run = run_value_momentum_mvp(
        history,
        config,
        frequency=args.frequency,
        portfolio_size=max(args.portfolio_size, 1),
        recommendation_size=max(args.recommendation_size, 1),
        max_periods=max(args.max_periods, 1),
        round_trip_cost_pct=cost_pct,
        slippage_pct=max(args.slippage_pct, 0.0),
        min_fundamental_coverage_pct=max(args.min_fundamental_coverage_pct, 0.0),
        min_periods_for_paper=max(args.min_periods_for_paper, 1),
        min_positions_for_paper=max(args.min_positions_for_paper, 1),
        data_status=data_status,
    )
    paths = write_outputs(run, policy_to_dict(policy), args=args)
    update_registry(args.registry, run["summary"], paths["experiment_dir"])

    summary = run["summary"]
    print("KR DayPilot value/quality + momentum MVP complete.")
    print(f"Experiment: {paths['experiment_dir']}")
    print(f"Frequency: {summary['frequency']}")
    print(f"Portfolio size: {summary['portfolio_size']}")
    print(f"Periods: {summary['periods']}")
    print(f"Promotion state: {summary['promotion_state']}")
    print(f"Average excess return: {summary['avg_excess_return_pct']}%")
    print(f"HTML: {paths['html']}")
    return 0


def run_value_momentum_mvp(
    history: pd.DataFrame,
    config: ValueMomentumConfig,
    *,
    frequency: str,
    portfolio_size: int,
    recommendation_size: int,
    max_periods: int,
    round_trip_cost_pct: float,
    slippage_pct: float,
    min_fundamental_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
    data_status: dict[str, object],
) -> dict[str, object]:
    days = sorted(history["source_bas_dt"].astype(str).unique())
    signal_days = build_rebalance_signal_days(days, frequency)
    periods = build_rebalance_periods(signal_days, days)
    if max_periods:
        periods = periods[-max_periods:]
    by_day = {str(day): frame.copy() for day, frame in history.groupby("source_bas_dt")}
    price_lookup = build_price_lookup(history)
    total_cost_pct = round_trip_cost_pct + slippage_pct

    trade_rows: list[dict[str, object]] = []
    period_rows: list[dict[str, object]] = []
    for period_number, period in enumerate(periods, start=1):
        signal_day = str(period["signal_day"])
        signal_rows = by_day.get(signal_day, pd.DataFrame())
        scored = score_value_momentum_candidates(signal_rows, config)
        selected = select_value_momentum_portfolio(signal_rows, config, portfolio_size=portfolio_size)
        returns: list[float] = []
        selected_records = selected.to_dict("records")
        for rank, candidate in enumerate(selected_records, start=1):
            ticker = str(candidate.get("ticker", "")).zfill(6)
            gross = position_return_pct(price_lookup, ticker, str(period["entry_day"]), str(period["exit_day"]))
            if gross is None:
                continue
            net = gross - total_cost_pct
            returns.append(net)
            trade_rows.append(
                {
                    "strategy_family": "value_quality_momentum_mvp",
                    "frequency": frequency,
                    "signal_day": display_day(signal_day),
                    "entry_date": display_day(period["entry_day"]),
                    "exit_date": display_day(period["exit_day"]),
                    "holding_days": period["holding_days"],
                    "rank": rank,
                    "ticker": ticker,
                    "company": candidate.get("company", ""),
                    "market": candidate.get("market", ""),
                    "weight": round(1.0 / max(len(selected_records), 1), 6),
                    "entry_price": price_lookup.get((str(period["entry_day"]), ticker), (0.0, 0.0))[0],
                    "exit_price": price_lookup.get((str(period["exit_day"]), ticker), (0.0, 0.0))[1],
                    "gross_return_pct": round(gross, 3),
                    "net_return_after_cost_pct": round(net, 3),
                    "vm_composite_score": candidate.get("vm_composite_score", ""),
                    "value_quality_score": candidate.get("value_quality_score", ""),
                    "momentum_score": candidate.get("momentum_score", ""),
                    "per": candidate.get("per", ""),
                    "pbr": candidate.get("pbr", ""),
                    "roe": candidate.get("roe", ""),
                    "ret_120d_pct": candidate.get("ret_120d_pct", ""),
                    "ret_240d_pct": candidate.get("ret_240d_pct", ""),
                    "relative_momentum_120d_pct": candidate.get("relative_momentum_120d_pct", ""),
                    "relative_momentum_240d_pct": candidate.get("relative_momentum_240d_pct", ""),
                    "fundamental_available": bool(candidate.get("fundamental_available", False)),
                    "fundamental_asof_dt": candidate.get("fundamental_asof_dt", ""),
                    "disclosure_risk_flag": bool(candidate.get("disclosure_risk_flag", False)),
                    "disclosure_event_types": candidate.get("disclosure_event_types", ""),
                }
            )
        benchmark_net = benchmark_return_pct(scored, config, price_lookup, str(period["entry_day"]), str(period["exit_day"]), total_cost_pct)
        period_net = float(pd.Series(returns).mean()) if returns else 0.0
        period_rows.append(
            {
                "strategy_family": "value_quality_momentum_mvp",
                "frequency": frequency,
                "period_number": period_number,
                "signal_day": display_day(signal_day),
                "entry_date": display_day(period["entry_day"]),
                "exit_date": display_day(period["exit_day"]),
                "holding_days": period["holding_days"],
                "candidates_scored": int(len(scored)),
                "candidates_passed": int((scored.get("candidate_status", "") == "pass").sum()) if not scored.empty else 0,
                "selected_positions": int(len(returns)),
                "portfolio_net_return_pct": round(period_net, 3),
                "benchmark_net_return_pct": round(benchmark_net, 3),
                "excess_return_pct": round(period_net - benchmark_net, 3),
            }
        )

    trades = pd.DataFrame(trade_rows)
    period_frame = pd.DataFrame(period_rows)
    latest_day = days[-1] if days else ""
    latest_scored = score_value_momentum_candidates(by_day.get(latest_day, pd.DataFrame()), config) if latest_day else pd.DataFrame()
    recommendations = (
        latest_scored[latest_scored["candidate_status"] == "pass"].head(recommendation_size).copy()
        if not latest_scored.empty
        else pd.DataFrame()
    )
    summary = summarize_run(
        trades,
        period_frame,
        recommendations,
        frequency=frequency,
        portfolio_size=portfolio_size,
        recommendation_size=recommendation_size,
        total_cost_pct=total_cost_pct,
        latest_day=latest_day,
        min_fundamental_coverage_pct=min_fundamental_coverage_pct,
        min_periods_for_paper=min_periods_for_paper,
        min_positions_for_paper=min_positions_for_paper,
        data_status=data_status,
    )
    return {
        "summary": summary,
        "portfolio_trades": trades,
        "portfolio_periods": period_frame,
        "latest_recommendations": recommendations,
        "data_status": data_status,
    }


def benchmark_return_pct(
    scored: pd.DataFrame,
    config: ValueMomentumConfig,
    price_lookup: dict[tuple[str, str], tuple[float, float]],
    entry_day: str,
    exit_day: str,
    total_cost_pct: float,
) -> float:
    if scored.empty:
        return 0.0
    universe = scored[
        scored["market"].isin(["KOSPI", "KOSDAQ"])
        & (pd.to_numeric(scored["market_cap"], errors="coerce").fillna(0.0) >= config.min_market_cap_krw)
        & (pd.to_numeric(scored["avg_value_20"], errors="coerce").fillna(0.0) >= config.min_avg_value_20d_krw)
    ].copy()
    returns = [
        gross - total_cost_pct
        for ticker in universe["ticker"].astype(str).str.zfill(6).tolist()
        if (gross := position_return_pct(price_lookup, ticker, entry_day, exit_day)) is not None
    ]
    return float(pd.Series(returns).mean()) if returns else 0.0


def summarize_run(
    trades: pd.DataFrame,
    periods: pd.DataFrame,
    recommendations: pd.DataFrame,
    *,
    frequency: str,
    portfolio_size: int,
    recommendation_size: int,
    total_cost_pct: float,
    latest_day: str,
    min_fundamental_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
    data_status: dict[str, object],
) -> dict[str, object]:
    period_returns = pd.to_numeric(periods.get("portfolio_net_return_pct", pd.Series(dtype=float)), errors="coerce").dropna()
    excess_returns = pd.to_numeric(periods.get("excess_return_pct", pd.Series(dtype=float)), errors="coerce").dropna()
    fundamental_coverage = pct(int(trades.get("fundamental_available", pd.Series(dtype=bool)).map(bool).sum()), len(trades)) if not trades.empty else 0.0
    avg_net = round(float(period_returns.mean()), 3) if not period_returns.empty else 0.0
    avg_excess = round(float(excess_returns.mean()), 3) if not excess_returns.empty else 0.0
    positive_rate = round(pct(int((period_returns > 0).sum()), len(period_returns)), 2)
    drawdown = round(max_drawdown(period_returns), 3)
    state, note = promotion_state(
        periods=int(len(periods)),
        positions=int(len(trades)),
        fundamental_coverage_pct=fundamental_coverage,
        avg_net_return_pct=avg_net,
        avg_excess_return_pct=avg_excess,
        positive_period_rate_pct=positive_rate,
        max_drawdown_pct=drawdown,
        min_fundamental_coverage_pct=min_fundamental_coverage_pct,
        min_periods_for_paper=min_periods_for_paper,
        min_positions_for_paper=min_positions_for_paper,
    )
    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "research_gate": "VALUE_MOMENTUM_MVP",
        "source_report_choice": "PDF Top 1: value/quality + momentum multifactor portfolio bot",
        "promotion_state": state,
        "data_note": note,
        "max_allowed_state": "paper_only",
        "live_order_automation": "disabled",
        "frequency": frequency,
        "portfolio_size": portfolio_size,
        "recommendation_size": recommendation_size,
        "latest_signal_day": display_day(latest_day),
        "latest_recommendations": int(len(recommendations)),
        "periods": int(len(periods)),
        "selected_positions": int(len(trades)),
        "avg_net_return_pct": avg_net,
        "cumulative_net_return_pct": round(compound_return(period_returns), 3),
        "avg_excess_return_pct": avg_excess,
        "positive_period_rate_pct": positive_rate,
        "max_drawdown_pct": drawdown,
        "fundamental_coverage_pct": round(fundamental_coverage, 2),
        "total_cost_pct": round(total_cost_pct, 3),
        "data_status": data_status,
        "note": "Implements the attached PDF's top-ranked PER/PBR/ROE plus 6-12 month market-relative momentum MVP. It generates recommendations only; live orders remain disabled.",
    }


def promotion_state(
    *,
    periods: int,
    positions: int,
    fundamental_coverage_pct: float,
    avg_net_return_pct: float,
    avg_excess_return_pct: float,
    positive_period_rate_pct: float,
    max_drawdown_pct: float,
    min_fundamental_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
) -> tuple[str, str]:
    if periods == 0 or positions == 0:
        return "no_signal", "No rebalance periods or selected positions."
    if fundamental_coverage_pct < min_fundamental_coverage_pct:
        return "needs_fundamental_data", f"Fundamental coverage {fundamental_coverage_pct:.2f}% is below required {min_fundamental_coverage_pct:.2f}%."
    if periods < min_periods_for_paper or positions < min_positions_for_paper:
        return "needs_more_sample", "Not enough chronological periods or selected positions for paper review."
    if avg_net_return_pct > 0 and avg_excess_return_pct > 0 and positive_period_rate_pct >= 50.0 and max_drawdown_pct >= -25.0:
        return "paper_only", "Research pass allows paper-only recommendation review. Live order automation remains disabled."
    return "failed_research", "Performance did not pass net return, benchmark, hit-rate, or drawdown gates."


def write_outputs(run: dict[str, object], policy: dict[str, object], *, args: argparse.Namespace) -> dict[str, Path]:
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    experiment_dir = args.experiments_dir / f"EXP_{stamp}_VALUE_MOMENTUM_MVP"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    args.latest_output.mkdir(parents=True, exist_ok=True)

    summary = run["summary"]
    trades = run["portfolio_trades"]
    periods = run["portfolio_periods"]
    recommendations = run["latest_recommendations"]
    config = {
        "args": {
            "frequency": args.frequency,
            "portfolio_size": args.portfolio_size,
            "recommendation_size": args.recommendation_size,
            "max_periods": args.max_periods,
            "round_trip_cost_pct": args.round_trip_cost_pct,
            "slippage_pct": args.slippage_pct,
            "min_fundamental_coverage_pct": args.min_fundamental_coverage_pct,
        },
        "policy": policy,
    }

    paths = {
        "config": experiment_dir / "config.json",
        "trades": experiment_dir / "portfolio_trades.csv",
        "periods": experiment_dir / "portfolio_periods.csv",
        "recommendations": experiment_dir / "latest_recommendations.csv",
        "json": experiment_dir / "metrics.json",
        "html": experiment_dir / "report.html",
    }
    paths["config"].write_text(json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_csv(trades, paths["trades"])
    write_csv(periods, paths["periods"])
    write_csv(recommendations, paths["recommendations"])
    payload = {
        "summary": summary,
        "latest_recommendations": records(recommendations),
        "periods": records(periods),
        "data_status": run["data_status"],
    }
    paths["json"].write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html = render_html(summary, recommendations, periods, trades, experiment_dir)
    paths["html"].write_text(html, encoding="utf-8-sig")

    (args.latest_output / "latest_summary.json").write_text(paths["json"].read_text(encoding="utf-8"), encoding="utf-8")
    (args.latest_output / "latest.html").write_text(html, encoding="utf-8-sig")
    write_csv(recommendations, args.latest_output / "latest_recommendations.csv")
    write_csv(periods, args.latest_output / "latest_periods.csv")
    write_csv(trades, args.latest_output / "latest_trades.csv")
    paths["experiment_dir"] = experiment_dir
    return paths


def update_registry(path: Path, summary: dict[str, object], experiment_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_at",
        "research_gate",
        "source_report_choice",
        "frequency",
        "portfolio_size",
        "promotion_state",
        "avg_net_return_pct",
        "avg_excess_return_pct",
        "positive_period_rate_pct",
        "max_drawdown_pct",
        "periods",
        "selected_positions",
        "fundamental_coverage_pct",
        "latest_recommendations",
        "experiment_dir",
    ]
    existing: list[dict[str, object]] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    existing.append(
        {
            "run_at": summary.get("generated_at", ""),
            "research_gate": summary.get("research_gate", ""),
            "source_report_choice": summary.get("source_report_choice", ""),
            "frequency": summary.get("frequency", ""),
            "portfolio_size": summary.get("portfolio_size", ""),
            "promotion_state": summary.get("promotion_state", ""),
            "avg_net_return_pct": summary.get("avg_net_return_pct", ""),
            "avg_excess_return_pct": summary.get("avg_excess_return_pct", ""),
            "positive_period_rate_pct": summary.get("positive_period_rate_pct", ""),
            "max_drawdown_pct": summary.get("max_drawdown_pct", ""),
            "periods": summary.get("periods", ""),
            "selected_positions": summary.get("selected_positions", ""),
            "fundamental_coverage_pct": summary.get("fundamental_coverage_pct", ""),
            "latest_recommendations": summary.get("latest_recommendations", ""),
            "experiment_dir": str(experiment_dir),
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)


def render_html(summary: dict[str, object], recommendations: pd.DataFrame, periods: pd.DataFrame, trades: pd.DataFrame, experiment_dir: Path) -> str:
    verdict = str(summary.get("promotion_state", "")).upper()
    recommendation_columns = [
        "ticker",
        "company",
        "market",
        "vm_composite_score",
        "value_quality_score",
        "momentum_score",
        "per",
        "pbr",
        "roe",
        "ret_120d_pct",
        "ret_240d_pct",
        "relative_momentum_120d_pct",
        "relative_momentum_240d_pct",
        "block_reason",
    ]
    recommendation_table = table(recommendations[[c for c in recommendation_columns if c in recommendations.columns]].copy())
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Value/Quality + Momentum MVP</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .note {{ color: #667085; line-height: 1.6; max-width: 1040px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 22px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 8px; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 6px 7px; text-align: right; }}
    table.data th:nth-child(1), table.data td:nth-child(1),
    table.data th:nth-child(2), table.data td:nth-child(2),
    table.data th:nth-child(3), table.data td:nth-child(3) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>Value/Quality + Momentum MVP</h1>
  <p class="note">
    첨부 PDF의 1순위 후보인 가치/퀄리티 + 모멘텀 멀티팩터 포트폴리오 봇입니다.
    PER, PBR, ROE와 6~12개월 시장 대비 상대 모멘텀을 결합해 장마감 후 확인할 후보를 생성합니다.
    이 리포트는 추천 및 paper_only 검토용이며 실전 주문 자동화는 비활성화되어 있습니다.
  </p>
  <div class="grid">
    {metric("Verdict", verdict)}
    {metric("Latest Signal", summary.get("latest_signal_day", ""))}
    {metric("Recommendations", summary.get("latest_recommendations", 0))}
    {metric("Frequency", summary.get("frequency", ""))}
    {metric("Portfolio Size", summary.get("portfolio_size", 0))}
    {metric("Avg Net", f"{summary.get('avg_net_return_pct', 0)}%")}
    {metric("Avg Excess", f"{summary.get('avg_excess_return_pct', 0)}%")}
    {metric("Max Drawdown", f"{summary.get('max_drawdown_pct', 0)}%")}
    {metric("Positive Rate", f"{summary.get('positive_period_rate_pct', 0)}%")}
    {metric("Fund Coverage", f"{summary.get('fundamental_coverage_pct', 0)}%")}
    {metric("Live Orders", summary.get("live_order_automation", "disabled"))}
    {metric("Total Cost", f"{summary.get('total_cost_pct', 0)}%")}
  </div>
  <p class="note">Experiment folder: {escape(str(experiment_dir))}</p>
  <p class="note">{escape(str(summary.get("data_note", "")))}</p>
  <h2>Latest Recommendations</h2>
  {recommendation_table}
  <h2>Rebalance Periods</h2>
  {table(periods)}
  <h2>Recent Trades</h2>
  {table(trades.tail(120) if not trades.empty else trades)}
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def table(frame: pd.DataFrame) -> str:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return '<p class="note">No rows.</p>'
    return frame.to_html(index=False, escape=True, classes="data")


def write_csv(frame: object, path: Path) -> None:
    if isinstance(frame, pd.DataFrame):
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return frame.to_dict("records") if isinstance(frame, pd.DataFrame) and not frame.empty else []


def json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            return value
    return value


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) * 100.0 if denominator else 0.0


def compound_return(returns_pct: pd.Series) -> float:
    if returns_pct.empty:
        return 0.0
    cumulative = (1.0 + returns_pct.fillna(0.0) / 100.0).prod() - 1.0
    return float(cumulative * 100.0)


if __name__ == "__main__":
    raise SystemExit(main())
