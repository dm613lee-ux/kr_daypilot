from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import add_daily_proxy_features, load_price_history
from .eod_context import add_eod_context_features
from .policy import load_policy, policy_to_dict
from .rg2_factor_engine import (
    FactorConfig,
    add_rg2_price_factors,
    build_rebalance_signal_days,
    load_fundamental_snapshots,
    merge_fundamental_snapshots,
    score_rg2_candidates,
    select_rg2_portfolio,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_EOD_CONTEXT = PROGRAM_ROOT / "data" / "eod_context"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_FUNDAMENTALS = PROGRAM_ROOT / "data" / "fundamentals" / "fundamental_snapshots.csv"
DEFAULT_KRX_VALUATION = PROGRAM_ROOT / "data" / "fundamentals" / "krx_valuation.csv"
DEFAULT_EXPERIMENTS = PROGRAM_ROOT / "experiments"
DEFAULT_REGISTRY = PROGRAM_ROOT / "experiments" / "registry_rg2.csv"
DEFAULT_LATEST_OUTPUT = PROGRAM_ROOT / "output" / "research_gate2"


@dataclass(frozen=True)
class StrategyProfile:
    experiment_id: str
    strategy_family: str
    description: str
    config: FactorConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Research Gate 2 multi-factor portfolio validation.")
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
    parser.add_argument("--max-periods", type=int, default=36)
    parser.add_argument("--window-periods", type=int, default=12)
    parser.add_argument("--round-trip-cost-pct", type=float, default=-1.0)
    parser.add_argument("--slippage-pct", type=float, default=0.2)
    parser.add_argument("--min-factor-groups", type=int, default=2)
    parser.add_argument("--min-fundamental-coverage-pct", type=float, default=50.0)
    parser.add_argument("--min-profitability-coverage-pct", type=float, default=50.0)
    parser.add_argument("--min-periods-for-paper", type=int, default=12)
    parser.add_argument("--min-positions-for-paper", type=int, default=80)
    parser.add_argument("--allow-risk-disclosure-penalty-only", action="store_true")
    args = parser.parse_args()

    policy = load_policy(args.policy)
    cost_pct = (
        policy.backtest_round_trip_cost_default_pct
        if args.round_trip_cost_pct < 0
        else float(args.round_trip_cost_pct)
    )
    base_config = FactorConfig(
        min_avg_value_20d_krw=policy.min_avg_trading_value_20d_krw,
        min_factor_groups=max(args.min_factor_groups, 1),
        block_risk_disclosures=not args.allow_risk_disclosure_penalty_only,
    )
    profiles = build_strategy_profiles(base_config)
    history, data_status = prepare_history(
        args.price_history,
        args.eod_context_dir,
        args.universe,
        args.fundamentals,
        args.krx_valuation,
    )
    run = run_research_gate2(
        history,
        profiles,
        frequency=args.frequency,
        portfolio_size=max(args.portfolio_size, 1),
        max_periods=max(args.max_periods, 1),
        window_periods=max(args.window_periods, 1),
        round_trip_cost_pct=cost_pct,
        slippage_pct=max(args.slippage_pct, 0.0),
        min_fundamental_coverage_pct=max(args.min_fundamental_coverage_pct, 0.0),
        min_profitability_coverage_pct=max(args.min_profitability_coverage_pct, 0.0),
        min_periods_for_paper=max(args.min_periods_for_paper, 1),
        min_positions_for_paper=max(args.min_positions_for_paper, 1),
        data_status=data_status,
    )
    paths = write_experiment_outputs(run, profiles, policy, args=args)
    update_registry(args.registry, run["strategy_metrics"], paths["experiment_dir"], run["summary"])

    summary = run["summary"]
    print("KR DayPilot Research Gate 2 complete.")
    print(f"Experiment: {paths['experiment_dir']}")
    print(f"Frequency: {summary['frequency']}")
    print(f"Portfolio size: {summary['portfolio_size']}")
    print(f"Periods: {summary['periods']}")
    print(f"Best strategy: {summary['best_strategy']}")
    print(f"Best average excess return: {summary['best_avg_excess_return_pct']}%")
    print(f"Gate pass: {summary['research_gate_pass']}")
    print(f"HTML: {paths['html']}")
    return 0


def build_strategy_profiles(base_config: FactorConfig) -> list[StrategyProfile]:
    return [
        StrategyProfile(
            "RG2-001",
            "rg2_multifactor_balanced",
            "Equal-weight value, profitability, momentum, and low-volatility factor ranking.",
            base_config,
        ),
        StrategyProfile(
            "RG2-002",
            "rg2_quality_value_momentum",
            "Quality and value anchored portfolio with momentum confirmation and lower low-vol tilt.",
            replace(base_config, value_weight=1.1, profitability_weight=1.3, momentum_weight=1.0, low_vol_weight=0.6),
        ),
        StrategyProfile(
            "RG2-003",
            "rg2_defensive_low_vol",
            "Defensive multi-factor portfolio emphasizing profitability and low realized volatility.",
            replace(base_config, value_weight=0.8, profitability_weight=1.1, momentum_weight=0.7, low_vol_weight=1.4),
        ),
    ]


def prepare_history(
    price_history: Path,
    eod_context_dir: Path,
    universe_path: Path,
    fundamentals_path: Path,
    krx_valuation_path: Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    history = add_daily_proxy_features(load_price_history(price_history))
    history = add_rg2_price_factors(history)
    history = add_eod_context_features(history, context_dir=eod_context_dir, universe_path=universe_path)
    fundamental_frames = []
    for path in [fundamentals_path, krx_valuation_path]:
        frame = load_fundamental_snapshots(path)
        if not frame.empty:
            fundamental_frames.append(frame)
    fundamentals = pd.concat(fundamental_frames, ignore_index=True) if fundamental_frames else pd.DataFrame()
    history = merge_fundamental_snapshots(history, fundamentals)
    data_status = {
        "price_history": str(price_history),
        "eod_context_dir": str(eod_context_dir),
        "universe": str(universe_path),
        "fundamentals": str(fundamentals_path),
        "krx_valuation": str(krx_valuation_path),
        "fundamental_rows": int(len(fundamentals)),
        "fundamental_data_available": bool(len(fundamentals) > 0),
    }
    return history.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True), data_status


def run_research_gate2(
    history: pd.DataFrame,
    profiles: list[StrategyProfile],
    *,
    frequency: str,
    portfolio_size: int,
    max_periods: int,
    window_periods: int,
    round_trip_cost_pct: float,
    slippage_pct: float,
    min_fundamental_coverage_pct: float,
    min_profitability_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
    data_status: dict[str, object],
) -> dict[str, object]:
    days = sorted(history["source_bas_dt"].astype(str).unique())
    signal_days = build_rebalance_signal_days(days, frequency)
    rebalance_periods = build_rebalance_periods(signal_days, days)
    if max_periods:
        rebalance_periods = rebalance_periods[-max_periods:]
    by_day = {str(day): frame.copy() for day, frame in history.groupby("source_bas_dt")}
    price_lookup = build_price_lookup(history)
    total_cost_pct = round_trip_cost_pct + slippage_pct

    trade_rows: list[dict[str, object]] = []
    period_rows: list[dict[str, object]] = []
    for profile in profiles:
        for period_number, period in enumerate(rebalance_periods, start=1):
            signal_day = period["signal_day"]
            signal_rows = by_day.get(signal_day, pd.DataFrame())
            scored = score_rg2_candidates(signal_rows, profile.config)
            selected = select_rg2_portfolio(signal_rows, profile.config, portfolio_size=portfolio_size)
            portfolio_returns: list[float] = []
            selected_records = selected.to_dict("records")
            for rank, candidate in enumerate(selected_records, start=1):
                ticker = str(candidate.get("ticker", "")).zfill(6)
                gross = position_return_pct(price_lookup, ticker, period["entry_day"], period["exit_day"])
                if gross is None:
                    continue
                net = gross - total_cost_pct
                portfolio_returns.append(net)
                trade_rows.append(
                    {
                        "experiment_id": profile.experiment_id,
                        "strategy_family": profile.strategy_family,
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
                        "entry_price": price_lookup.get((period["entry_day"], ticker), (0.0, 0.0))[0],
                        "exit_price": price_lookup.get((period["exit_day"], ticker), (0.0, 0.0))[1],
                        "gross_return_pct": round(gross, 3),
                        "net_return_after_cost_pct": round(net, 3),
                        "round_trip_cost_pct": round(round_trip_cost_pct, 3),
                        "slippage_pct": round(slippage_pct, 3),
                        "rg2_composite_score": candidate.get("rg2_composite_score", 0.0),
                        "value_score": candidate.get("value_score", ""),
                        "profitability_score": candidate.get("profitability_score", ""),
                        "momentum_score": candidate.get("momentum_score", ""),
                        "low_vol_score": candidate.get("low_vol_score", ""),
                        "factor_group_count": candidate.get("factor_group_count", 0),
                        "fundamental_group_count": candidate.get("fundamental_group_count", 0),
                        "fundamental_available": bool(candidate.get("fundamental_available", False)),
                        "fundamental_asof_dt": candidate.get("fundamental_asof_dt", ""),
                        "disclosure_risk_flag": bool(candidate.get("disclosure_risk_flag", False)),
                        "disclosure_event_types": candidate.get("disclosure_event_types", ""),
                    }
                )
            benchmark_net = benchmark_return_pct(scored, profile.config, price_lookup, period["entry_day"], period["exit_day"], total_cost_pct)
            period_net = float(pd.Series(portfolio_returns).mean()) if portfolio_returns else 0.0
            period_rows.append(
                {
                    "experiment_id": profile.experiment_id,
                    "strategy_family": profile.strategy_family,
                    "frequency": frequency,
                    "period_number": period_number,
                    "signal_day": display_day(signal_day),
                    "entry_date": display_day(period["entry_day"]),
                    "exit_date": display_day(period["exit_day"]),
                    "holding_days": period["holding_days"],
                    "candidates_scored": int(len(scored)),
                    "candidates_passed": int((scored.get("candidate_status", "") == "pass").sum()) if not scored.empty else 0,
                    "candidates_blocked": int((scored.get("candidate_status", "") == "blocked").sum()) if not scored.empty else 0,
                    "selected_positions": int(len(portfolio_returns)),
                    "portfolio_net_return_pct": round(period_net, 3),
                    "benchmark_net_return_pct": round(benchmark_net, 3),
                    "excess_return_pct": round(period_net - benchmark_net, 3),
                }
            )

    trades = pd.DataFrame(trade_rows)
    periods = pd.DataFrame(period_rows)
    metrics = summarize_strategies(
        trades,
        periods,
        profiles,
        min_fundamental_coverage_pct=min_fundamental_coverage_pct,
        min_profitability_coverage_pct=min_profitability_coverage_pct,
        min_periods_for_paper=min_periods_for_paper,
        min_positions_for_paper=min_positions_for_paper,
    )
    windows = summarize_windows(periods, window_periods=window_periods)
    summary = summarize_gate(
        metrics,
        periods,
        frequency=frequency,
        portfolio_size=portfolio_size,
        total_cost_pct=total_cost_pct,
        data_status=data_status,
    )
    return {
        "summary": summary,
        "portfolio_trades": trades,
        "portfolio_periods": periods,
        "strategy_metrics": metrics,
        "walk_forward_windows": windows,
        "data_status": data_status,
    }


def build_rebalance_periods(signal_days: list[str], days: list[str]) -> list[dict[str, object]]:
    day_positions = {day: pos for pos, day in enumerate(days)}
    periods: list[dict[str, object]] = []
    for idx, signal_day in enumerate(signal_days[:-1]):
        signal_pos = day_positions.get(signal_day)
        exit_day = signal_days[idx + 1]
        exit_pos = day_positions.get(exit_day)
        if signal_pos is None or exit_pos is None or signal_pos + 1 >= len(days):
            continue
        entry_day = days[signal_pos + 1]
        entry_pos = day_positions[entry_day]
        if entry_pos > exit_pos:
            continue
        periods.append(
            {
                "signal_day": signal_day,
                "entry_day": entry_day,
                "exit_day": exit_day,
                "holding_days": exit_pos - entry_pos + 1,
            }
        )
    return periods


def build_price_lookup(history: pd.DataFrame) -> dict[tuple[str, str], tuple[float, float]]:
    lookup: dict[tuple[str, str], tuple[float, float]] = {}
    for row in history[["source_bas_dt", "ticker", "open", "close"]].itertuples(index=False):
        lookup[(str(row.source_bas_dt), str(row.ticker).zfill(6))] = (float(row.open), float(row.close))
    return lookup


def position_return_pct(price_lookup: dict[tuple[str, str], tuple[float, float]], ticker: str, entry_day: str, exit_day: str) -> float | None:
    entry = price_lookup.get((entry_day, ticker))
    exit_ = price_lookup.get((exit_day, ticker))
    if not entry or not exit_:
        return None
    entry_open = entry[0]
    exit_close = exit_[1]
    if entry_open <= 0 or exit_close <= 0:
        return None
    return (exit_close / entry_open - 1.0) * 100.0


def benchmark_return_pct(
    scored: pd.DataFrame,
    config: FactorConfig,
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


def summarize_strategies(
    trades: pd.DataFrame,
    periods: pd.DataFrame,
    profiles: list[StrategyProfile],
    *,
    min_fundamental_coverage_pct: float,
    min_profitability_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for profile in profiles:
        strategy_periods = periods[periods["strategy_family"] == profile.strategy_family].copy() if not periods.empty else pd.DataFrame()
        strategy_trades = trades[trades["strategy_family"] == profile.strategy_family].copy() if not trades.empty else pd.DataFrame()
        period_returns = pd.to_numeric(strategy_periods.get("portfolio_net_return_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        benchmark_returns = pd.to_numeric(strategy_periods.get("benchmark_net_return_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        excess_returns = pd.to_numeric(strategy_periods.get("excess_return_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        fundamental_coverage = pct(int(strategy_trades.get("fundamental_available", pd.Series(dtype=bool)).sum()), len(strategy_trades))
        profitability_scores = pd.to_numeric(strategy_trades.get("profitability_score", pd.Series(dtype=float)), errors="coerce")
        profitability_coverage = pct(int(profitability_scores.notna().sum()), len(strategy_trades))
        metrics = {
            "experiment_id": profile.experiment_id,
            "strategy_family": profile.strategy_family,
            "description": profile.description,
            "periods": int(len(strategy_periods)),
            "selected_positions": int(len(strategy_trades)),
            "avg_positions_per_period": round(float(strategy_periods.get("selected_positions", pd.Series(dtype=float)).mean()), 2)
            if not strategy_periods.empty
            else 0.0,
            "avg_net_return_pct": round(float(period_returns.mean()), 3) if not period_returns.empty else 0.0,
            "median_net_return_pct": round(float(period_returns.median()), 3) if not period_returns.empty else 0.0,
            "cumulative_net_return_pct": round(compound_return(period_returns), 3),
            "avg_benchmark_return_pct": round(float(benchmark_returns.mean()), 3) if not benchmark_returns.empty else 0.0,
            "cumulative_benchmark_return_pct": round(compound_return(benchmark_returns), 3),
            "avg_excess_return_pct": round(float(excess_returns.mean()), 3) if not excess_returns.empty else 0.0,
            "positive_period_rate_pct": round(pct(int((period_returns > 0).sum()), len(period_returns)), 2),
            "beat_benchmark_rate_pct": round(pct(int((excess_returns > 0).sum()), len(excess_returns)), 2),
            "max_drawdown_pct": round(max_drawdown(period_returns), 3),
            "fundamental_coverage_pct": round(fundamental_coverage, 2),
            "profitability_coverage_pct": round(profitability_coverage, 2),
            "avg_factor_group_count": round(float(strategy_trades.get("factor_group_count", pd.Series(dtype=float)).mean()), 2)
            if not strategy_trades.empty
            else 0.0,
            "promotion_state": "",
            "data_note": "",
        }
        state, note = promotion_state(
            metrics,
            min_fundamental_coverage_pct=min_fundamental_coverage_pct,
            min_profitability_coverage_pct=min_profitability_coverage_pct,
            min_periods_for_paper=min_periods_for_paper,
            min_positions_for_paper=min_positions_for_paper,
        )
        metrics["promotion_state"] = state
        metrics["data_note"] = note
        rows.append(metrics)
    return pd.DataFrame(rows)


def promotion_state(
    metrics: dict[str, object],
    *,
    min_fundamental_coverage_pct: float,
    min_profitability_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
) -> tuple[str, str]:
    periods = int(metrics.get("periods", 0))
    positions = int(metrics.get("selected_positions", 0))
    fundamental_coverage = float(metrics.get("fundamental_coverage_pct", 0.0))
    profitability_coverage = float(metrics.get("profitability_coverage_pct", 0.0))
    avg_net = float(metrics.get("avg_net_return_pct", 0.0))
    avg_excess = float(metrics.get("avg_excess_return_pct", 0.0))
    positive_rate = float(metrics.get("positive_period_rate_pct", 0.0))
    drawdown = float(metrics.get("max_drawdown_pct", 0.0))
    if periods == 0 or positions == 0:
        return "no_signal", "No rebalance periods or selected positions."
    if fundamental_coverage < min_fundamental_coverage_pct:
        return (
            "needs_fundamental_data",
            f"Fundamental coverage {fundamental_coverage:.2f}% is below required {min_fundamental_coverage_pct:.2f}%.",
        )
    if profitability_coverage < min_profitability_coverage_pct:
        return (
            "needs_profitability_data",
            f"Profitability coverage {profitability_coverage:.2f}% is below required {min_profitability_coverage_pct:.2f}%.",
        )
    if periods < min_periods_for_paper or positions < min_positions_for_paper:
        return "needs_more_sample", "Not enough chronological periods or selected positions for paper review."
    if avg_net > 0 and avg_excess > 0 and positive_rate >= 50.0 and drawdown >= -25.0:
        return "paper_only", "Research pass allows paper-only review. Live order automation remains disabled."
    return "failed_research", "Performance did not pass net return, benchmark, hit-rate, or drawdown gates."


def summarize_windows(periods: pd.DataFrame, *, window_periods: int) -> pd.DataFrame:
    if periods.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for strategy, frame in periods.groupby("strategy_family"):
        ordered = frame.sort_values("period_number").reset_index(drop=True)
        for start in range(0, len(ordered), window_periods):
            window = ordered.iloc[start : start + window_periods].copy()
            if window.empty:
                continue
            returns = pd.to_numeric(window["portfolio_net_return_pct"], errors="coerce").dropna()
            excess = pd.to_numeric(window["excess_return_pct"], errors="coerce").dropna()
            rows.append(
                {
                    "strategy_family": strategy,
                    "window": len(rows) + 1,
                    "window_start": window["entry_date"].iloc[0],
                    "window_end": window["exit_date"].iloc[-1],
                    "periods": int(len(window)),
                    "avg_net_return_pct": round(float(returns.mean()), 3) if not returns.empty else 0.0,
                    "cumulative_net_return_pct": round(compound_return(returns), 3),
                    "avg_excess_return_pct": round(float(excess.mean()), 3) if not excess.empty else 0.0,
                    "positive_period_rate_pct": round(pct(int((returns > 0).sum()), len(returns)), 2),
                }
            )
    return pd.DataFrame(rows)


def summarize_gate(
    metrics: pd.DataFrame,
    periods: pd.DataFrame,
    *,
    frequency: str,
    portfolio_size: int,
    total_cost_pct: float,
    data_status: dict[str, object],
) -> dict[str, object]:
    if metrics.empty:
        return {
            "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
            "research_gate": "RG2",
            "research_gate_pass": False,
            "frequency": frequency,
            "portfolio_size": portfolio_size,
            "periods": 0,
            "best_strategy": "",
        }
    comparable = metrics.sort_values(["avg_excess_return_pct", "avg_net_return_pct", "selected_positions"], ascending=[False, False, False])
    best = comparable.iloc[0]
    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "research_gate": "RG2",
        "research_gate_pass": bool((metrics["promotion_state"] == "paper_only").any()),
        "max_allowed_state": "paper_only",
        "live_order_automation": "disabled",
        "frequency": frequency,
        "portfolio_size": portfolio_size,
        "periods": int(periods["period_number"].max()) if not periods.empty else 0,
        "strategies": int(metrics["strategy_family"].nunique()),
        "best_strategy": str(best.get("strategy_family", "")),
        "best_avg_net_return_pct": float(best.get("avg_net_return_pct", 0.0)),
        "best_avg_excess_return_pct": float(best.get("avg_excess_return_pct", 0.0)),
        "total_cost_pct": round(total_cost_pct, 3),
        "data_status": data_status,
        "note": "RG2 validates multi-factor portfolios with chronological rebalancing. Passing strategies may be paper_only, never live.",
    }


def write_experiment_outputs(
    run: dict[str, object],
    profiles: list[StrategyProfile],
    policy: object,
    *,
    args: argparse.Namespace,
) -> dict[str, Path]:
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    experiment_dir = args.experiments_dir / f"EXP_{stamp}_RG2"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    args.latest_output.mkdir(parents=True, exist_ok=True)

    trades = run["portfolio_trades"]
    periods = run["portfolio_periods"]
    metrics = run["strategy_metrics"]
    windows = run["walk_forward_windows"]
    summary = run["summary"]

    config = {
        "args": {
            "frequency": args.frequency,
            "portfolio_size": args.portfolio_size,
            "max_periods": args.max_periods,
            "window_periods": args.window_periods,
            "round_trip_cost_pct": args.round_trip_cost_pct,
            "slippage_pct": args.slippage_pct,
            "min_factor_groups": args.min_factor_groups,
            "min_fundamental_coverage_pct": args.min_fundamental_coverage_pct,
            "min_profitability_coverage_pct": args.min_profitability_coverage_pct,
            "allow_risk_disclosure_penalty_only": args.allow_risk_disclosure_penalty_only,
        },
        "policy": policy_to_dict(policy),
        "profiles": [
            {
                "experiment_id": profile.experiment_id,
                "strategy_family": profile.strategy_family,
                "description": profile.description,
                "config": profile.config.__dict__,
            }
            for profile in profiles
        ],
    }
    config_path = experiment_dir / "config.json"
    trades_path = experiment_dir / "portfolio_trades.csv"
    periods_path = experiment_dir / "portfolio_periods.csv"
    metrics_path = experiment_dir / "strategy_metrics.csv"
    windows_path = experiment_dir / "walk_forward_windows.csv"
    json_path = experiment_dir / "metrics.json"
    html_path = experiment_dir / "report.html"

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(trades, trades_path)
    write_csv(periods, periods_path)
    write_csv(metrics, metrics_path)
    write_csv(windows, windows_path)
    json_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "strategy_metrics": records(metrics),
                "data_status": run["data_status"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    html = render_html(summary, metrics, windows, periods, trades, experiment_dir)
    html_path.write_text(html, encoding="utf-8-sig")
    (args.latest_output / "latest_summary.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    (args.latest_output / "latest.html").write_text(html, encoding="utf-8-sig")
    write_csv(metrics, args.latest_output / "latest_strategy_metrics.csv")
    write_csv(periods, args.latest_output / "latest_periods.csv")
    return {
        "experiment_dir": experiment_dir,
        "config": config_path,
        "trades": trades_path,
        "periods": periods_path,
        "metrics": metrics_path,
        "windows": windows_path,
        "json": json_path,
        "html": html_path,
    }


def update_registry(path: Path, metrics: pd.DataFrame, experiment_dir: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_at",
        "research_gate",
        "experiment_id",
        "strategy_family",
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
        "profitability_coverage_pct",
        "data_note",
        "experiment_dir",
    ]
    existing: list[dict[str, object]] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    run_at = str(summary.get("generated_at", datetime.now(tz=KST).isoformat(timespec="seconds")))
    for row in records(metrics):
        existing.append(
            {
                "run_at": run_at,
                "research_gate": "RG2",
                "experiment_id": row.get("experiment_id", ""),
                "strategy_family": row.get("strategy_family", ""),
                "frequency": summary.get("frequency", ""),
                "portfolio_size": summary.get("portfolio_size", ""),
                "promotion_state": row.get("promotion_state", ""),
                "avg_net_return_pct": row.get("avg_net_return_pct", ""),
                "avg_excess_return_pct": row.get("avg_excess_return_pct", ""),
                "positive_period_rate_pct": row.get("positive_period_rate_pct", ""),
                "max_drawdown_pct": row.get("max_drawdown_pct", ""),
                "periods": row.get("periods", ""),
                "selected_positions": row.get("selected_positions", ""),
                "fundamental_coverage_pct": row.get("fundamental_coverage_pct", ""),
                "profitability_coverage_pct": row.get("profitability_coverage_pct", ""),
                "data_note": row.get("data_note", ""),
                "experiment_dir": str(experiment_dir),
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)


def render_html(
    summary: dict[str, object],
    metrics: pd.DataFrame,
    windows: pd.DataFrame,
    periods: pd.DataFrame,
    trades: pd.DataFrame,
    experiment_dir: Path,
) -> str:
    verdict = "PAPER ONLY PASS" if summary.get("research_gate_pass") else "NO PAPER-READY MULTIFACTOR STRATEGY"
    metrics_html = table(metrics)
    windows_html = table(windows.tail(60) if not windows.empty else windows)
    periods_html = table(periods.tail(80) if not periods.empty else periods)
    trades_html = table(trades.tail(120) if not trades.empty else trades)
    data_status = summary.get("data_status", {})
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Research Gate 2</title>
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
    code {{ background: #f2f4f7; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>KR DayPilot Research Gate 2</h1>
  <p class="note">
    국내주식 다요인 추천엔진 검증 리포트입니다. 가치, 수익성, 모멘텀, 저변동성 점수를 결합하고
    공시 리스크 이벤트는 차단 또는 감점합니다. 이 산출물은 리서치와 페이퍼 검증 전용이며
    실전 주문 자동화는 포함하지 않습니다.
  </p>
  <div class="grid">
    {metric("Verdict", verdict)}
    {metric("Frequency", summary.get("frequency", ""))}
    {metric("Portfolio Size", summary.get("portfolio_size", 0))}
    {metric("Periods", summary.get("periods", 0))}
    {metric("Strategies", summary.get("strategies", 0))}
    {metric("Best Strategy", summary.get("best_strategy", ""))}
    {metric("Best Avg Net", f"{summary.get('best_avg_net_return_pct', 0)}%")}
    {metric("Best Avg Excess", f"{summary.get('best_avg_excess_return_pct', 0)}%")}
    {metric("Total Cost", f"{summary.get('total_cost_pct', 0)}%")}
    {metric("Max State", summary.get("max_allowed_state", "paper_only"))}
    {metric("Live Orders", summary.get("live_order_automation", "disabled"))}
    {metric("Fundamental Rows", data_status.get("fundamental_rows", 0))}
  </div>
  <p class="note">Experiment folder: {escape(str(experiment_dir))}</p>
  <p class="note">
    재무 데이터 기본 경로: <code>{escape(str(data_status.get("fundamentals", "")))}</code>,
    KRX 가치지표 경로: <code>{escape(str(data_status.get("krx_valuation", "")))}</code>
  </p>
  <h2>Strategy Metrics</h2>
  {metrics_html}
  <h2>Chronological Walk-Forward Windows</h2>
  {windows_html}
  <h2>Recent Rebalance Periods</h2>
  {periods_html}
  <h2>Recent Portfolio Positions</h2>
  {trades_html}
</body>
</html>"""


def table(frame: pd.DataFrame) -> str:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return "<p class=\"note\">No rows.</p>"
    return frame.to_html(index=False, escape=True, classes="data")


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def write_csv(frame: object, path: Path) -> None:
    if isinstance(frame, pd.DataFrame):
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return frame.to_dict("records") if isinstance(frame, pd.DataFrame) and not frame.empty else []


def compound_return(returns_pct: pd.Series) -> float:
    if returns_pct.empty:
        return 0.0
    cumulative = (1.0 + returns_pct.fillna(0.0) / 100.0).prod() - 1.0
    return float(cumulative * 100.0)


def max_drawdown(returns_pct: pd.Series) -> float:
    if returns_pct.empty:
        return 0.0
    equity = (1.0 + returns_pct.fillna(0.0) / 100.0).cumprod()
    peak = equity.cummax()
    drawdown = (equity / peak - 1.0) * 100.0
    return float(drawdown.min())


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) * 100.0 if denominator else 0.0


def display_day(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
