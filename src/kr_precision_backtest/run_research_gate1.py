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
from .policy import Policy, load_policy, policy_to_dict
from .swing_strategy import add_swing_features


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_EOD_CONTEXT = PROGRAM_ROOT / "data" / "eod_context"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_HYPOTHESES = PROGRAM_ROOT / "experiments" / "strategy_hypotheses.json"
DEFAULT_REGISTRY = PROGRAM_ROOT / "experiments" / "registry.csv"
DEFAULT_EXPERIMENTS = PROGRAM_ROOT / "experiments"
DEFAULT_LATEST_OUTPUT = PROGRAM_ROOT / "output" / "research_gate1"
MIN_MARKET_CAP_KRW = 100_000_000_000
MIN_REFERENCE_VALUE_KRW = 5_000_000_000


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    entry_offset_pct: float
    take_profit_pct: float
    stop_loss_pct: float
    min_gap_pct: float
    max_gap_pct: float
    max_down_gap_pct: float
    hold_days: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Research Gate 1 strategy-family comparison.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--eod-context-dir", type=Path, default=DEFAULT_EOD_CONTEXT)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--hypotheses", type=Path, default=DEFAULT_HYPOTHESES)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    parser.add_argument("--max-reference-days", type=int, default=250)
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--window-days", type=int, default=20)
    parser.add_argument("--execution-profiles", default="all")
    args = parser.parse_args()

    policy = replace(load_policy(args.policy), swing_hold_days=max(args.hold_days, 1))
    execution_profiles = build_execution_profiles(policy, args.execution_profiles)
    hypotheses = load_hypotheses(args.hypotheses)
    history = prepare_history(args.price_history, args.eod_context_dir, args.universe)
    run = run_research_gate1(
        history,
        hypotheses,
        policy,
        execution_profiles,
        max_reference_days=max(args.max_reference_days, 1),
        max_candidates=max(args.max_candidates, 1),
        window_days=max(args.window_days, 1),
    )
    paths = write_experiment_outputs(
        run,
        policy,
        args=args,
        experiments_dir=args.experiments_dir,
        latest_output=args.latest_output,
    )
    update_registry(args.registry, run["strategy_metrics"], paths["experiment_dir"])

    summary = run["summary"]
    print("KR DayPilot Research Gate 1 complete.")
    print(f"Experiment: {paths['experiment_dir']}")
    print(f"Strategies: {summary['strategies']}")
    print(f"Total paper orders: {summary['paper_orders']}")
    print(f"Total filled: {summary['paper_filled']}")
    print(f"Best strategy: {summary['best_strategy']}")
    print(f"Best execution: {summary.get('best_execution_profile', '')}")
    print(f"Gate pass: {summary['research_gate_pass']}")
    print(f"HTML: {paths['html']}")
    return 0


def load_hypotheses(path: Path) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Strategy hypotheses not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_execution_profiles(policy: Policy, requested: str) -> list[ExecutionProfile]:
    hold_days = max(int(policy.swing_hold_days), 1)
    profiles = [
        ExecutionProfile("pullback_limit", -0.5, 3.0, 2.0, 0.5, 4.0, -4.0, hold_days),
        ExecutionProfile("close_confirm", 0.0, 2.5, 1.8, 0.0, 4.5, -3.0, hold_days),
        ExecutionProfile("strength_follow", 0.2, 3.0, 2.0, 0.3, 5.0, -2.0, hold_days),
        ExecutionProfile("wide_swing", -0.5, 5.0, 3.0, -0.5, 5.0, -5.0, hold_days),
        ExecutionProfile("tight_risk", -0.3, 2.0, 1.2, 0.0, 4.0, -2.0, min(hold_days, 3)),
    ]
    if requested.strip().lower() == "all":
        return profiles
    names = {name.strip() for name in requested.split(",") if name.strip()}
    selected = [profile for profile in profiles if profile.name in names]
    if not selected:
        raise ValueError(f"No execution profiles matched: {requested}")
    return selected


def prepare_history(price_history: Path, eod_context_dir: Path, universe_path: Path) -> pd.DataFrame:
    history = add_swing_features(add_daily_proxy_features(load_price_history(price_history)))
    history = add_eod_context_features(history, context_dir=eod_context_dir, universe_path=universe_path)
    history = add_research_features(history)
    return history.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def add_research_features(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    grouped = df.groupby("ticker", group_keys=False)
    df["close_3_ago"] = grouped["close"].shift(3)
    df["close_60_ago"] = grouped["close"].shift(60)
    df["ret_3d_pct"] = (df["close"] / df["close_3_ago"] - 1.0) * 100.0
    df["ret_60d_pct"] = (df["close"] / df["close_60_ago"] - 1.0) * 100.0
    df["flow_stabilization_score"] = (
        num(df, "foreign_net_buy_value_z20")
        + num(df, "institution_net_buy_value_z20")
        - num(df, "retail_net_buy_value_z20").clip(lower=0.0)
    ).fillna(0.0)
    df["sector_residual_5d_pct"] = num(df, "sector_relative_strength_5d_pct")
    df["low_volume_pullback"] = num(df, "value_ratio_20") <= 1.2
    df["event_allowed"] = ~bool_series(df, "disclosure_risk_flag")
    return df


def run_research_gate1(
    history: pd.DataFrame,
    hypotheses: dict[str, object],
    policy: Policy,
    execution_profiles: list[ExecutionProfile],
    *,
    max_reference_days: int,
    max_candidates: int,
    window_days: int,
) -> dict[str, object]:
    days = sorted(history["source_bas_dt"].astype(str).unique())
    eligible_days = days[80 : -policy.swing_hold_days]
    reference_days = eligible_days[-max_reference_days:]
    by_day = {day: frame.copy() for day, frame in history.groupby("source_bas_dt")}
    by_ticker = {ticker: frame.sort_values("source_bas_dt").reset_index(drop=True) for ticker, frame in history.groupby("ticker")}
    hypothesis_rows = list(hypotheses.get("hypotheses", []))

    trade_rows: list[dict[str, object]] = []
    data_notes: list[dict[str, object]] = []
    for item in hypothesis_rows:
        if not isinstance(item, dict):
            continue
        family = str(item.get("strategy_family", ""))
        experiment_id = str(item.get("experiment_id", family))
        for reference_day in reference_days:
            day_rows = by_day.get(reference_day, pd.DataFrame())
            candidates, note = select_strategy_candidates(
                day_rows,
                family=family,
                max_candidates=max_candidates,
                policy=policy,
            )
            if note and reference_day == reference_days[-1]:
                data_notes.append({"experiment_id": experiment_id, "strategy_family": family, "note": note})
            for rank, candidate in enumerate(candidates.to_dict("records"), start=1):
                ticker = str(candidate.get("ticker", "")).zfill(6)
                for profile in execution_profiles:
                    trade_rows.append(
                        simulate_paper_trade(
                            candidate,
                            by_ticker.get(ticker, pd.DataFrame()),
                            reference_day,
                            policy,
                            profile,
                            experiment_id=experiment_id,
                            strategy_family=family,
                            order_rank=rank,
                        )
                    )

    trades = pd.DataFrame(trade_rows)
    metrics = summarize_by_strategy(trades, hypothesis_rows, policy, execution_profiles, data_notes)
    windows = summarize_windows(trades, window_days=window_days, policy=policy)
    summary = summarize_gate(metrics, trades, reference_days, policy)
    return {
        "summary": summary,
        "trades": trades,
        "strategy_metrics": metrics,
        "walk_forward_windows": windows,
        "hypotheses": hypotheses,
        "data_notes": data_notes,
    }


def select_strategy_candidates(
    day_rows: pd.DataFrame,
    *,
    family: str,
    max_candidates: int,
    policy: Policy,
) -> tuple[pd.DataFrame, str]:
    if day_rows.empty:
        return pd.DataFrame(), "no day rows"
    df = normalize_candidate_frame(day_rows)
    base = base_tradeable_mask(df, policy)

    if family == "sector_relative_oversold_flow_reversal":
        mask = (
            base
            & (df["sector_residual_5d_pct"] <= -2.0)
            & (df["ret_5d_pct"] <= -1.0)
            & (df["ret_5d_pct"] >= -20.0)
            & (df["flow_stabilization_score"] >= -1.0)
            & (df["value_ratio_20"].between(0.8, 8.0))
        )
        score = (
            (-df["sector_residual_5d_pct"]).clip(0, 20) * 2.0
            + df["flow_stabilization_score"].clip(-2, 6) * 4.0
            + df["value_ratio_20"].clip(0, 5) * 2.0
            + df["close_location_pct"].clip(0, 100) / 10.0
        )
    elif family == "momentum_pullback_strong_regime":
        mask = (
            base
            & (df["ret_20d_pct"] >= 8.0)
            & (df["ret_60d_pct"] >= 12.0)
            & (df["ret_5d_pct"].between(-6.0, 3.0))
            & (df["ret_1d_pct"] > -8.0)
            & (df["market_regime"].isin(["strong", "neutral"]))
            & (df["sector_residual_5d_pct"] >= -2.5)
        )
        score = (
            df["ret_60d_pct"].clip(0, 80) * 0.35
            + df["ret_20d_pct"].clip(0, 40) * 0.8
            - df["ret_5d_pct"].abs().clip(0, 10)
            + df["sector_residual_5d_pct"].clip(-5, 10) * 1.5
            + df["close_location_pct"].clip(0, 100) / 12.0
        )
    elif family == "momentum_pullback_quality_regime":
        mask = (
            base
            & (df["ret_20d_pct"] >= 12.0)
            & (df["ret_60d_pct"] >= 20.0)
            & (df["ret_5d_pct"].between(-5.0, 1.5))
            & (df["ret_1d_pct"] > -5.0)
            & (df["market_regime"].isin(["strong", "neutral"]))
            & (df["sector_residual_5d_pct"] >= 0.0)
            & (df["value_ratio_20"].between(0.5, 2.2))
            & (df["close_location_pct"] >= 45.0)
            & (df["flow_stabilization_score"] >= -1.5)
        )
        score = (
            df["ret_60d_pct"].clip(0, 90) * 0.3
            + df["ret_20d_pct"].clip(0, 50) * 0.9
            + df["sector_residual_5d_pct"].clip(0, 12) * 2.0
            + df["flow_stabilization_score"].clip(-2, 6) * 2.0
            + (2.2 - df["value_ratio_20"]).clip(0, 2.2) * 2.0
            + df["close_location_pct"].clip(0, 100) / 10.0
        )
    elif family == "momentum_pullback_deep_rebound":
        mask = (
            base
            & (df["ret_20d_pct"] >= 10.0)
            & (df["ret_60d_pct"] >= 18.0)
            & (df["ret_5d_pct"].between(-10.0, -3.0))
            & (df["ret_3d_pct"] > -8.0)
            & (df["close_location_pct"] >= 55.0)
            & (df["sector_residual_5d_pct"] >= -1.5)
            & (df["flow_stabilization_score"] >= -0.5)
            & (df["value_ratio_20"].between(0.8, 5.0))
        )
        score = (
            (-df["ret_5d_pct"]).clip(0, 12) * 2.0
            + df["ret_60d_pct"].clip(0, 90) * 0.25
            + df["sector_residual_5d_pct"].clip(-2, 10) * 1.5
            + df["flow_stabilization_score"].clip(-1, 8) * 3.0
            + df["close_location_pct"].clip(0, 100) / 8.0
        )
    elif family == "momentum_pullback_deep_rebound_broad":
        mask = (
            base
            & (df["ret_20d_pct"] >= 8.0)
            & (df["ret_60d_pct"] >= 12.0)
            & (df["ret_5d_pct"].between(-12.0, -2.0))
            & (df["ret_3d_pct"] > -10.0)
            & (df["close_location_pct"] >= 50.0)
            & (df["sector_residual_5d_pct"] >= -3.0)
            & (df["flow_stabilization_score"] >= -2.0)
            & (df["value_ratio_20"].between(0.7, 6.0))
        )
        score = (
            (-df["ret_5d_pct"]).clip(0, 14) * 1.7
            + df["ret_60d_pct"].clip(0, 90) * 0.22
            + df["ret_20d_pct"].clip(0, 50) * 0.45
            + df["sector_residual_5d_pct"].clip(-3, 10) * 1.1
            + df["flow_stabilization_score"].clip(-2, 8) * 2.0
            + df["close_location_pct"].clip(0, 100) / 9.0
        )
    elif family == "momentum_breakout_continuation":
        mask = (
            base
            & (df["ret_20d_pct"] >= 15.0)
            & (df["ret_60d_pct"] >= 25.0)
            & (df["ret_5d_pct"].between(1.0, 12.0))
            & (df["ret_1d_pct"].between(-2.0, 8.0))
            & (df["market_regime"].isin(["strong", "neutral"]))
            & (df["sector_residual_5d_pct"] >= 1.0)
            & (df["value_ratio_20"].between(1.0, 5.0))
            & (df["close_location_pct"] >= 65.0)
        )
        score = (
            df["ret_20d_pct"].clip(0, 60) * 0.7
            + df["ret_60d_pct"].clip(0, 120) * 0.2
            + df["sector_residual_5d_pct"].clip(0, 15) * 2.0
            + df["value_ratio_20"].clip(0, 5) * 1.5
            + df["close_location_pct"].clip(0, 100) / 12.0
        )
    elif family == "event_overreaction_hard_gate":
        event_text = df.get("disclosure_event_types", pd.Series("", index=df.index)).fillna("").astype(str)
        hard_block = event_text.str.contains("financing_risk|governance_risk", case=False, regex=True)
        mask = (
            base
            & (df["disclosure_count"] > 0)
            & (~hard_block)
            & (df["ret_1d_pct"].between(-12.0, -1.0))
            & (df["value_ratio_20"].between(1.0, 8.0))
        )
        score = (
            (-df["ret_1d_pct"]).clip(0, 15) * 2.0
            + df["disclosure_count"].clip(0, 5) * 2.0
            + df["value_ratio_20"].clip(0, 5) * 1.5
            + df["close_location_pct"].clip(0, 100) / 20.0
        )
    elif family == "sector_pair_residual_mean_reversion":
        mask = (
            base
            & (df["sector_residual_5d_pct"] <= -4.0)
            & (df["ret_5d_pct"] >= -25.0)
            & (df["market_regime"].isin(["neutral", "strong"]))
            & (df["value_ratio_20"].between(0.7, 6.0))
        )
        score = (
            (-df["sector_residual_5d_pct"]).clip(0, 25) * 2.5
            + df["market_advancing_ratio"].clip(0, 1) * 10.0
            + df["trading_value_z_20"].clip(-2, 5)
        )
    elif family == "orderflow_execution_timing":
        required = {"orderbook_imbalance", "trade_strength", "vwap_distance"}
        missing = sorted(required.difference(df.columns))
        if missing:
            return pd.DataFrame(), f"missing execution data: {', '.join(missing)}"
        mask = (
            base
            & (pd.to_numeric(df["orderbook_imbalance"], errors="coerce") > 0.1)
            & (pd.to_numeric(df["trade_strength"], errors="coerce") > 100.0)
            & (pd.to_numeric(df["vwap_distance"], errors="coerce").between(-1.0, 1.5))
        )
        score = (
            pd.to_numeric(df["orderbook_imbalance"], errors="coerce").fillna(0.0) * 30.0
            + pd.to_numeric(df["trade_strength"], errors="coerce").fillna(0.0) / 10.0
            - pd.to_numeric(df["vwap_distance"], errors="coerce").fillna(0.0).abs() * 3.0
        )
    else:
        return pd.DataFrame(), f"unsupported strategy family: {family}"

    selected = df[mask].copy()
    if selected.empty:
        return selected, ""
    selected["strategy_score"] = score.loc[selected.index].round(4)
    selected["strategy_family"] = family
    selected["strategy_block_reason"] = ""
    return selected.sort_values(["strategy_score", "trading_value"], ascending=[False, False]).head(max_candidates).reset_index(drop=True), ""


def normalize_candidate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    numeric_columns = [
        "ret_1d_pct",
        "ret_3d_pct",
        "ret_5d_pct",
        "ret_20d_pct",
        "ret_60d_pct",
        "value_ratio_20",
        "trading_value_z_20",
        "close_location_pct",
        "sector_residual_5d_pct",
        "flow_stabilization_score",
        "market_cap",
        "avg_value_20",
        "trading_value",
        "close",
        "market_advancing_ratio",
        "disclosure_count",
    ]
    for column in numeric_columns:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    if "market_regime" not in df.columns:
        df["market_regime"] = "neutral"
    if "disclosure_risk_flag" not in df.columns:
        df["disclosure_risk_flag"] = False
    df["disclosure_risk_flag"] = bool_series(df, "disclosure_risk_flag")
    return df


def base_tradeable_mask(df: pd.DataFrame, policy: Policy) -> pd.Series:
    return (
        df["market"].isin(["KOSPI", "KOSDAQ"])
        & (df["avg_value_20"] >= policy.min_avg_trading_value_20d_krw)
        & (df["trading_value"] >= MIN_REFERENCE_VALUE_KRW)
        & (df["market_cap"] >= MIN_MARKET_CAP_KRW)
        & (df["close"] >= 1000.0)
        & (~df["disclosure_risk_flag"])
    )


def simulate_paper_trade(
    candidate: dict[str, object],
    ticker_rows: pd.DataFrame,
    reference_day: str,
    policy: Policy,
    profile: ExecutionProfile,
    *,
    experiment_id: str,
    strategy_family: str,
    order_rank: int,
) -> dict[str, object]:
    reference_close = positive(candidate.get("close"))
    entry_limit = reference_close * (1.0 + profile.entry_offset_pct / 100.0)
    target = entry_limit * (1.0 + profile.take_profit_pct / 100.0)
    stop = entry_limit * (1.0 - profile.stop_loss_pct / 100.0)
    future = ticker_rows[ticker_rows["source_bas_dt"].astype(str) > reference_day].sort_values("source_bas_dt").head(profile.hold_days)
    base = {
        "experiment_id": experiment_id,
        "strategy_family": strategy_family,
        "execution_profile": profile.name,
        "reference_day": display_day(reference_day),
        "paper_order_rank": order_rank,
        "ticker": str(candidate.get("ticker", "")).zfill(6),
        "company": candidate.get("company", ""),
        "market": candidate.get("market", ""),
        "strategy_score": round(float(candidate.get("strategy_score", 0.0)), 4),
        "reference_close": round(reference_close, 2),
        "entry_limit_price": round(entry_limit, 2),
        "target_price": round(target, 2),
        "stop_price": round(stop, 2),
        "entry_offset_pct": profile.entry_offset_pct,
        "take_profit_pct": profile.take_profit_pct,
        "stop_loss_pct": profile.stop_loss_pct,
        "holding_days": profile.hold_days,
        "ret_1d_pct": round(float(candidate.get("ret_1d_pct", 0.0)), 3),
        "ret_3d_pct": round(float(candidate.get("ret_3d_pct", 0.0)), 3),
        "ret_5d_pct": round(float(candidate.get("ret_5d_pct", 0.0)), 3),
        "ret_20d_pct": round(float(candidate.get("ret_20d_pct", 0.0)), 3),
        "ret_60d_pct": round(float(candidate.get("ret_60d_pct", 0.0)), 3),
        "sector_residual_5d_pct": round(float(candidate.get("sector_residual_5d_pct", 0.0)), 3),
        "flow_stabilization_score": round(float(candidate.get("flow_stabilization_score", 0.0)), 3),
        "market_regime": str(candidate.get("market_regime", "")),
        "disclosure_count": int(float(candidate.get("disclosure_count", 0.0))),
    }
    if future.empty or reference_close <= 0:
        return {**base, **empty_label("no_future_data")}

    entry_day = future.iloc[0]
    entry_open = positive(entry_day.get("open"))
    entry_low = positive(entry_day.get("low"))
    entry_high = positive(entry_day.get("high"))
    if entry_open <= 0 or entry_low <= 0 or entry_high <= 0:
        return {**base, **empty_label("bad_entry_bar")}

    gap_pct = (entry_open / reference_close - 1.0) * 100.0
    if gap_pct < profile.max_down_gap_pct:
        return {**base, **no_fill_label("gap_down_execution_block", future, entry_limit, gap_pct)}
    if gap_pct < profile.min_gap_pct:
        return {**base, **no_fill_label("morning_confirmation_missing", future, entry_limit, gap_pct)}
    if gap_pct > profile.max_gap_pct:
        return {**base, **no_fill_label("gap_up_execution_block", future, entry_limit, gap_pct)}
    if not (entry_low <= entry_limit <= max(entry_high, entry_open)):
        return {**base, **no_fill_label("limit_not_touched", future, entry_limit, gap_pct)}

    max_adverse = 0.0
    max_favorable = 0.0
    exit_day = display_day(future.iloc[-1].get("source_bas_dt", ""))
    exit_price = positive(future.iloc[-1].get("close"))
    exit_reason = "time_exit"

    for _, bar in future.iterrows():
        high = positive(bar.get("high"))
        low = positive(bar.get("low"))
        close = positive(bar.get("close"))
        if high <= 0 or low <= 0 or close <= 0:
            continue
        max_adverse = min(max_adverse, (low / entry_limit - 1.0) * 100.0)
        max_favorable = max(max_favorable, (high / entry_limit - 1.0) * 100.0)
        hit_target = high >= target
        hit_stop = low <= stop
        exit_day = display_day(bar.get("source_bas_dt", ""))
        if hit_target and hit_stop:
            exit_reason = "ambiguous_stop_first"
            exit_price = stop
            break
        if hit_stop:
            exit_reason = "stop_hit"
            exit_price = stop
            break
        if hit_target:
            exit_reason = "target_hit"
            exit_price = target
            break
        exit_price = close

    gross = (exit_price / entry_limit - 1.0) * 100.0
    net = gross - policy.backtest_round_trip_cost_default_pct
    return {
        **base,
        "entry_date": display_day(entry_day.get("source_bas_dt", "")),
        "paper_filled": True,
        "fill_price": round(entry_limit, 2),
        "entry_gap_pct": round(gap_pct, 3),
        "exit_date": exit_day,
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "target_hit_d1_d5": exit_reason == "target_hit",
        "stop_hit_d1_d5": exit_reason in {"stop_hit", "ambiguous_stop_first"},
        "time_exit": exit_reason == "time_exit",
        "max_adverse_excursion_pct": round(max_adverse, 3),
        "max_favorable_excursion_pct": round(max_favorable, 3),
        "gross_return_pct": round(gross, 3),
        "net_return_after_cost_pct": round(net, 3),
        "missed_trade_outcome": 0.0,
        "label_note": "",
    }


def empty_label(note: str) -> dict[str, object]:
    return {
        "entry_date": "",
        "paper_filled": False,
        "fill_price": 0.0,
        "entry_gap_pct": 0.0,
        "exit_date": "",
        "exit_price": 0.0,
        "exit_reason": "no_fill",
        "target_hit_d1_d5": False,
        "stop_hit_d1_d5": False,
        "time_exit": False,
        "max_adverse_excursion_pct": 0.0,
        "max_favorable_excursion_pct": 0.0,
        "gross_return_pct": 0.0,
        "net_return_after_cost_pct": 0.0,
        "missed_trade_outcome": 0.0,
        "label_note": note,
    }


def no_fill_label(note: str, future: pd.DataFrame, entry_limit: float, gap_pct: float) -> dict[str, object]:
    max_high = float(pd.to_numeric(future["high"], errors="coerce").max()) if not future.empty else 0.0
    missed = (max_high / entry_limit - 1.0) * 100.0 if entry_limit > 0 and max_high > 0 else 0.0
    return {
        **empty_label(note),
        "entry_date": display_day(future.iloc[0].get("source_bas_dt", "")) if not future.empty else "",
        "entry_gap_pct": round(gap_pct, 3),
        "missed_trade_outcome": round(missed, 3),
    }


def summarize_by_strategy(
    trades: pd.DataFrame,
    hypotheses: list[object],
    policy: Policy,
    execution_profiles: list[ExecutionProfile],
    data_notes: list[dict[str, object]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    note_map = {str(item["strategy_family"]): str(item["note"]) for item in data_notes}
    for item in hypotheses:
        if not isinstance(item, dict):
            continue
        experiment_id = str(item.get("experiment_id", ""))
        family = str(item.get("strategy_family", ""))
        for profile in execution_profiles:
            frame = (
                trades[(trades["strategy_family"] == family) & (trades["execution_profile"] == profile.name)].copy()
                if not trades.empty and "execution_profile" in trades.columns
                else pd.DataFrame()
            )
            metrics = summarize_frame(frame, policy)
            state = promotion_state(metrics, note_map.get(family, ""))
            rows.append(
                {
                    "experiment_id": experiment_id,
                    "strategy_family": family,
                    "execution_profile": profile.name,
                    "promotion_state": state,
                    "data_note": note_map.get(family, ""),
                    **metrics,
                }
            )
    return pd.DataFrame(rows)


def summarize_frame(frame: pd.DataFrame, policy: Policy) -> dict[str, object]:
    if frame.empty:
        return empty_metrics()
    orders = frame[frame["paper_order_rank"] <= policy.max_order_candidates].copy()
    filled = orders[orders["paper_filled"] == True].copy()  # noqa: E712
    target_hits = int((filled["target_hit_d1_d5"] == True).sum()) if not filled.empty else 0  # noqa: E712
    stop_hits = int((filled["stop_hit_d1_d5"] == True).sum()) if not filled.empty else 0  # noqa: E712
    time_exits = int((filled["time_exit"] == True).sum()) if not filled.empty else 0  # noqa: E712
    wilson_low, wilson_high = wilson(target_hits, len(filled))
    avg_net = float(filled["net_return_after_cost_pct"].mean()) if not filled.empty else 0.0
    median_net = float(filled["net_return_after_cost_pct"].median()) if not filled.empty else 0.0
    return {
        "recommendations": int(len(frame)),
        "paper_orders": int(len(orders)),
        "paper_filled": int(len(filled)),
        "fill_rate": round(pct(len(filled), len(orders)), 2),
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "time_exits": time_exits,
        "target_rate": round(pct(target_hits, len(filled)), 2),
        "stop_rate": round(pct(stop_hits, len(filled)), 2),
        "time_exit_rate": round(pct(time_exits, len(filled)), 2),
        "target_wilson_low": round(wilson_low, 2),
        "target_wilson_high": round(wilson_high, 2),
        "avg_net_return_after_cost_pct": round(avg_net, 3),
        "median_net_return_after_cost_pct": round(median_net, 3),
        "max_consecutive_stops": max_consecutive_stops(filled),
        "required_oos_trades": policy.oos_min_trades,
    }


def empty_metrics() -> dict[str, object]:
    return {
        "recommendations": 0,
        "paper_orders": 0,
        "paper_filled": 0,
        "fill_rate": 0.0,
        "target_hits": 0,
        "stop_hits": 0,
        "time_exits": 0,
        "target_rate": 0.0,
        "stop_rate": 0.0,
        "time_exit_rate": 0.0,
        "target_wilson_low": 0.0,
        "target_wilson_high": 0.0,
        "avg_net_return_after_cost_pct": 0.0,
        "median_net_return_after_cost_pct": 0.0,
        "max_consecutive_stops": 0,
        "required_oos_trades": 0,
    }


def promotion_state(metrics: dict[str, object], data_note: str) -> str:
    filled = int(metrics.get("paper_filled", 0))
    avg_net = float(metrics.get("avg_net_return_after_cost_pct", 0.0))
    stop_rate = float(metrics.get("stop_rate", 0.0))
    target_rate = float(metrics.get("target_rate", 0.0))
    if data_note:
        return "data_unavailable"
    if filled == 0:
        return "no_signal"
    if filled < 100:
        return "needs_more_sample"
    if avg_net > 0 and target_rate > stop_rate and stop_rate <= 45.0:
        return "paper_only_candidate"
    return "failed_research"


def summarize_windows(trades: pd.DataFrame, *, window_days: int, policy: Policy) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    group_columns = ["strategy_family", "execution_profile"] if "execution_profile" in trades.columns else ["strategy_family"]
    for group_key, family_frame in trades.groupby(group_columns):
        if isinstance(group_key, tuple):
            family, profile = group_key
        else:
            family, profile = group_key, ""
        dates = sorted(family_frame["reference_day"].dropna().astype(str).unique())
        for start in range(0, len(dates), window_days):
            window_dates = dates[start : start + window_days]
            if not window_dates:
                continue
            window = family_frame[family_frame["reference_day"].isin(window_dates)].copy()
            rows.append(
                {
                    "strategy_family": family,
                    "execution_profile": profile,
                    "window": len(rows) + 1,
                    "window_start": window_dates[0],
                    "window_end": window_dates[-1],
                    **summarize_frame(window, policy),
                }
            )
    return pd.DataFrame(rows)


def summarize_gate(metrics: pd.DataFrame, trades: pd.DataFrame, reference_days: list[str], policy: Policy) -> dict[str, object]:
    if metrics.empty:
        return {
            "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
            "strategies": 0,
            "research_gate_pass": False,
            "best_strategy": "",
        }
    comparable = metrics[pd.to_numeric(metrics["paper_filled"], errors="coerce").fillna(0) > 0].copy()
    ranked = (comparable if not comparable.empty else metrics).sort_values(
        ["avg_net_return_after_cost_pct", "target_rate", "paper_filled"],
        ascending=[False, False, False],
    )
    best = ranked.iloc[0]
    pass_states = {"paper_only_candidate"}
    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "reference_start": display_day(reference_days[0]) if reference_days else "",
        "reference_end": display_day(reference_days[-1]) if reference_days else "",
        "reference_days": len(reference_days),
        "strategies": int(metrics["strategy_family"].nunique()),
        "strategy_combinations": int(len(metrics)),
        "execution_profiles": int(metrics["execution_profile"].nunique()) if "execution_profile" in metrics.columns else 0,
        "recommendations": int(len(trades)),
        "paper_orders": int(metrics["paper_orders"].sum()),
        "paper_filled": int(metrics["paper_filled"].sum()),
        "best_strategy": str(best.get("strategy_family", "")),
        "best_execution_profile": str(best.get("execution_profile", "")),
        "best_avg_net_return_after_cost_pct": float(best.get("avg_net_return_after_cost_pct", 0.0)),
        "research_gate_pass": bool(metrics["promotion_state"].isin(pass_states).any()),
        "hold_days": policy.swing_hold_days,
        "cost_pct": policy.backtest_round_trip_cost_default_pct,
        "note": "Research Gate 1 compares independent strategy families. A pass here only allows paper research, not live trading.",
    }


def write_experiment_outputs(
    run: dict[str, object],
    policy: Policy,
    *,
    args: argparse.Namespace,
    experiments_dir: Path,
    latest_output: Path,
) -> dict[str, Path]:
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    experiment_dir = experiments_dir / f"EXP_{stamp}_RG1"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    latest_output.mkdir(parents=True, exist_ok=True)

    trades = run["trades"]
    metrics = run["strategy_metrics"]
    windows = run["walk_forward_windows"]
    summary = run["summary"]

    config_path = experiment_dir / "config.json"
    trades_path = experiment_dir / "trades.csv"
    metrics_path = experiment_dir / "strategy_metrics.csv"
    windows_path = experiment_dir / "walk_forward_windows.csv"
    summary_path = experiment_dir / "metrics.json"
    html_path = experiment_dir / "report.html"

    config = {
        "args": {
            "max_reference_days": args.max_reference_days,
            "max_candidates": args.max_candidates,
            "hold_days": args.hold_days,
            "window_days": args.window_days,
            "execution_profiles": args.execution_profiles,
        },
        "policy": policy_to_dict(policy),
        "hypotheses": run["hypotheses"],
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(trades, trades_path)
    write_csv(metrics, metrics_path)
    write_csv(windows, windows_path)
    summary_path.write_text(
        json.dumps({"summary": summary, "strategy_metrics": records(metrics)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html = render_html(summary, metrics, windows, trades, experiment_dir)
    html_path.write_text(html, encoding="utf-8-sig")

    latest_json = latest_output / "latest_summary.json"
    latest_html = latest_output / "latest.html"
    latest_json.write_text(summary_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {
        "experiment_dir": experiment_dir,
        "config": config_path,
        "trades": trades_path,
        "metrics": metrics_path,
        "windows": windows_path,
        "json": summary_path,
        "html": html_path,
        "latest_html": latest_html,
    }


def write_csv(frame: object, path: Path) -> None:
    if isinstance(frame, pd.DataFrame):
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")


def update_registry(path: Path, metrics: pd.DataFrame, experiment_dir: Path) -> None:
    if not path.exists() or metrics.empty:
        return
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    metric_map = best_metric_by_experiment(metrics)
    now = datetime.now(tz=KST).isoformat(timespec="seconds")
    fieldnames = list(rows[0].keys()) if rows else []
    for extra in [
        "last_run_at",
        "last_run_experiment",
        "last_promotion_state",
        "last_best_execution_profile",
        "last_avg_net_return_after_cost_pct",
        "last_paper_filled",
    ]:
        if extra not in fieldnames:
            fieldnames.append(extra)
    for row in rows:
        metric = metric_map.get(str(row.get("experiment_id", "")))
        if not metric:
            continue
        row["status"] = str(metric.get("promotion_state", row.get("status", "")))
        row["last_run_at"] = now
        row["last_run_experiment"] = str(experiment_dir)
        row["last_promotion_state"] = str(metric.get("promotion_state", ""))
        row["last_best_execution_profile"] = str(metric.get("execution_profile", ""))
        row["last_avg_net_return_after_cost_pct"] = str(metric.get("avg_net_return_after_cost_pct", ""))
        row["last_paper_filled"] = str(metric.get("paper_filled", ""))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def best_metric_by_experiment(metrics: pd.DataFrame) -> dict[str, dict[str, object]]:
    if metrics.empty:
        return {}
    comparable = metrics.copy()
    comparable["_filled"] = pd.to_numeric(comparable.get("paper_filled", 0), errors="coerce").fillna(0)
    comparable["_avg"] = pd.to_numeric(comparable.get("avg_net_return_after_cost_pct", 0), errors="coerce").fillna(-999)
    comparable["_target"] = pd.to_numeric(comparable.get("target_rate", 0), errors="coerce").fillna(0)
    result: dict[str, dict[str, object]] = {}
    for experiment_id, group in comparable.groupby("experiment_id"):
        with_fills = group[group["_filled"] > 0]
        ranked = (with_fills if not with_fills.empty else group).sort_values(
            ["_avg", "_target", "_filled"], ascending=[False, False, False]
        )
        result[str(experiment_id)] = ranked.drop(columns=["_filled", "_avg", "_target"]).iloc[0].to_dict()
    return result


def render_html(summary: dict[str, object], metrics: pd.DataFrame, windows: pd.DataFrame, trades: pd.DataFrame, experiment_dir: Path) -> str:
    metrics_html = metrics.to_html(index=False, escape=True, classes="data") if not metrics.empty else "<p>No strategy metrics.</p>"
    window_preview = windows.tail(40) if not windows.empty else pd.DataFrame()
    windows_html = window_preview.to_html(index=False, escape=True, classes="data") if not window_preview.empty else "<p>No window metrics.</p>"
    trades_preview = trades.tail(80) if not trades.empty else pd.DataFrame()
    trades_html = trades_preview.to_html(index=False, escape=True, classes="data") if not trades_preview.empty else "<p>No trades.</p>"
    verdict = "PASS FOR PAPER REVIEW" if summary.get("research_gate_pass") else "NO PAPER-READY STRATEGY"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Research Gate 1</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .note {{ color: #667085; line-height: 1.6; max-width: 980px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 22px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 24px; font-weight: 700; margin-top: 8px; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 6px 7px; text-align: right; }}
    table.data th:nth-child(1), table.data td:nth-child(1),
    table.data th:nth-child(2), table.data td:nth-child(2),
    table.data th:nth-child(3), table.data td:nth-child(3) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot Research Gate 1</h1>
  <p class="note">
    This report compares independent strategy families under the same paper-order
    labels, cost model, and risk gate. This is research output only. It is not a
    live trading recommendation.
  </p>
  <div class="grid">
    {metric("Verdict", verdict)}
    {metric("Strategies", summary.get("strategies", 0))}
    {metric("Combinations", summary.get("strategy_combinations", 0))}
    {metric("Paper Orders", summary.get("paper_orders", 0))}
    {metric("Filled", summary.get("paper_filled", 0))}
    {metric("Best Strategy", summary.get("best_strategy", ""))}
    {metric("Best Execution", summary.get("best_execution_profile", ""))}
    {metric("Best Avg Net", f"{summary.get('best_avg_net_return_after_cost_pct', 0)}%")}
    {metric("Reference Days", summary.get("reference_days", 0))}
    {metric("Hold Days", summary.get("hold_days", 0))}
  </div>
  <p class="note">Experiment folder: {escape(str(experiment_dir))}</p>
  <h2>Strategy Metrics</h2>
  {metrics_html}
  <h2>Chronological Window Preview</h2>
  {windows_html}
  <h2>Recent Trade Labels</h2>
  {trades_html}
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return frame.to_dict("records") if isinstance(frame, pd.DataFrame) and not frame.empty else []


def num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def bool_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    return df[column].map(lambda value: str(value).strip().lower() in {"1", "true", "t", "y", "yes", "risk", "위험"}).fillna(False)


def positive(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def pct(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) * 100.0 if denominator else 0.0


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.96
    phat = successes / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    margin = z * ((phat * (1 - phat) + z * z / (4 * total)) / total) ** 0.5 / denom
    return max((center - margin) * 100.0, 0.0), min((center + margin) * 100.0, 100.0)


def max_consecutive_stops(filled: pd.DataFrame) -> int:
    if filled.empty or "stop_hit_d1_d5" not in filled.columns:
        return 0
    current = 0
    best = 0
    ordered = filled.sort_values(["reference_day", "paper_order_rank"])
    for hit in ordered["stop_hit_d1_d5"].tolist():
        if bool(hit):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def display_day(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
