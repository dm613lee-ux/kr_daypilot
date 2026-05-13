from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from .policy import Policy
from .strategy import select_candidates


@dataclass(frozen=True)
class BacktestOptions:
    max_reference_days: int = 250


def run_backtest(history: pd.DataFrame, policy: Policy, options: BacktestOptions) -> tuple[pd.DataFrame, dict[str, object]]:
    days = sorted(history["source_bas_dt"].astype(str).unique())
    eligible_days = days[60:-1]
    reference_days = eligible_days[-options.max_reference_days :]
    rows: list[dict[str, object]] = []

    by_day = {day: frame.copy() for day, frame in history.groupby("source_bas_dt")}
    by_ticker = {ticker: frame.sort_values("source_bas_dt").reset_index(drop=True) for ticker, frame in history.groupby("ticker")}

    for reference_day in reference_days:
        day_rows = by_day.get(reference_day, pd.DataFrame())
        candidates = select_candidates(day_rows, policy)
        for rank, candidate in enumerate(candidates.to_dict("records"), start=1):
            outcome = _simulate_trade(candidate, by_ticker.get(str(candidate["ticker"]), pd.DataFrame()), reference_day, policy)
            rows.append(
                {
                    "reference_day": _fmt_day(reference_day),
                    "rank": rank,
                    "ticker": str(candidate["ticker"]).zfill(6),
                    "company": candidate.get("company", ""),
                    "market": candidate.get("market", ""),
                    "signal_score": candidate.get("signal_score", 0),
                    "day_change_pct": round(float(candidate.get("day_change_pct", 0.0)), 2),
                    "market_median_change_pct": round(float(candidate.get("market_median_change_pct", 0.0)), 2),
                    "trading_value": int(float(candidate.get("trading_value", 0.0))),
                    "avg_value_20": int(float(candidate.get("avg_value_20", 0.0))),
                    "value_ratio_20": round(float(candidate.get("value_ratio_20", 0.0)), 2),
                    "close_location_pct": round(float(candidate.get("close_location_pct", 0.0)), 1),
                    "lower_tail_recovery_pct": round(float(candidate.get("lower_tail_recovery_pct", 0.0)), 2),
                    "close_vs_open_pct": round(float(candidate.get("close_vs_open_pct", 0.0)), 2),
                    "distance_from_60d_high_pct": round(float(candidate.get("distance_from_60d_high_pct", 0.0)), 2),
                    **outcome,
                }
            )

    results = pd.DataFrame(rows)
    return results, summarize(results, reference_days=reference_days, policy=policy)


def summarize(results: pd.DataFrame, *, reference_days: list[str], policy: Policy) -> dict[str, object]:
    if results.empty:
        return {
            "reference_days": len(reference_days),
            "recommendations": 0,
            "entries": 0,
            "successes": 0,
            "failures": 0,
            "time_exits": 0,
            "success_rate": 0.0,
            "wilson_low": 0.0,
            "wilson_high": 0.0,
            "avg_net_return_pct": 0.0,
            "median_net_return_pct": 0.0,
            "profit_factor": 0.0,
            "max_consecutive_losses": 0,
            "research_pass": False,
            "limitation": _limitation_note(),
        }

    entries = results[results["exit_reason"] != "no_entry"].copy()
    successes = int((entries["exit_reason"] == "target_hit").sum())
    failures = int((entries["exit_reason"].isin(["stop_loss", "ambiguous_stop_first"])).sum())
    time_exits = int((entries["exit_reason"] == "time_exit").sum())
    success_rate = _rate(successes, len(entries))
    wilson_low, wilson_high = _wilson(successes, len(entries))
    positive_sum = float(entries.loc[entries["net_return_pct"] > 0, "net_return_pct"].sum()) if not entries.empty else 0.0
    negative_sum = abs(float(entries.loc[entries["net_return_pct"] < 0, "net_return_pct"].sum())) if not entries.empty else 0.0
    profit_factor = positive_sum / negative_sum if negative_sum > 0 else (positive_sum if positive_sum > 0 else 0.0)
    max_consecutive_losses = _max_consecutive_losses(entries)
    avg_net = float(entries["net_return_pct"].mean()) if not entries.empty else 0.0
    median_net = float(entries["net_return_pct"].median()) if not entries.empty else 0.0

    research_pass = (
        len(entries) >= policy.oos_min_trades
        and success_rate >= policy.research_pass_success_rate_pct
        and wilson_low >= policy.research_wilson_lower_pct
        and avg_net > 0
    )
    return {
        "reference_start": _fmt_day(reference_days[0]) if reference_days else "",
        "reference_end": _fmt_day(reference_days[-1]) if reference_days else "",
        "reference_days": len(reference_days),
        "recommendations": int(len(results)),
        "entries": int(len(entries)),
        "no_entries": int((results["exit_reason"] == "no_entry").sum()),
        "successes": successes,
        "failures": failures,
        "time_exits": time_exits,
        "success_rate": round(success_rate, 2),
        "failure_rate": round(_rate(failures, len(entries)), 2),
        "time_exit_rate": round(_rate(time_exits, len(entries)), 2),
        "wilson_low": round(wilson_low, 2),
        "wilson_high": round(wilson_high, 2),
        "avg_net_return_pct": round(avg_net, 3),
        "median_net_return_pct": round(median_net, 3),
        "profit_factor": round(profit_factor, 3),
        "max_consecutive_losses": max_consecutive_losses,
        "research_pass": research_pass,
        "required_oos_trades": policy.oos_min_trades,
        "required_success_rate": policy.research_pass_success_rate_pct,
        "required_wilson_low": policy.research_wilson_lower_pct,
        "limitation": _limitation_note(),
    }


def _simulate_trade(candidate: dict[str, object], ticker_rows: pd.DataFrame, reference_day: str, policy: Policy) -> dict[str, object]:
    future = ticker_rows[ticker_rows["source_bas_dt"].astype(str) > reference_day].sort_values("source_bas_dt")
    if future.empty:
        return _outcome("no_entry", "", 0.0, 0.0, 0.0, 0.0, "no_future_data")

    bar = future.iloc[0]
    reference_close = _positive(candidate.get("close"))
    entry_open = _positive(bar.get("open"))
    high = _positive(bar.get("high"))
    low = _positive(bar.get("low"))
    close = _positive(bar.get("close"))
    entry_day = _fmt_day(str(bar.get("source_bas_dt", "")))

    if reference_close <= 0 or entry_open <= 0 or high <= 0 or low <= 0:
        return _outcome("no_entry", entry_day, 0.0, 0.0, 0.0, 0.0, "bad_price_data")

    gap_pct = (entry_open / reference_close - 1.0) * 100.0
    if gap_pct > 1.0:
        return _outcome("no_entry", entry_day, 0.0, 0.0, 0.0, gap_pct, "gap_up_chase_block")
    if gap_pct < -5.0:
        return _outcome("no_entry", entry_day, 0.0, 0.0, 0.0, gap_pct, "gap_down_risk_block")

    entry = entry_open
    target = entry * (1.0 + policy.take_profit_pct / 100.0)
    stop = entry * (1.0 - policy.stop_loss_pct / 100.0)
    hit_target = high >= target
    hit_stop = low <= stop

    if hit_stop and hit_target:
        return _priced_outcome("ambiguous_stop_first", entry_day, entry, stop, target, stop, gap_pct, policy)
    if hit_stop:
        return _priced_outcome("stop_loss", entry_day, entry, stop, target, stop, gap_pct, policy)
    if hit_target:
        return _priced_outcome("target_hit", entry_day, entry, target, target, stop, gap_pct, policy)
    return _priced_outcome("time_exit", entry_day, entry, close, target, stop, gap_pct, policy)


def _priced_outcome(
    reason: str,
    entry_day: str,
    entry: float,
    exit_price: float,
    target: float,
    stop: float,
    gap_pct: float,
    policy: Policy,
) -> dict[str, object]:
    gross_return = (exit_price / entry - 1.0) * 100.0
    net_return = gross_return - policy.backtest_round_trip_cost_default_pct
    return _outcome(reason, entry_day, entry, target, stop, net_return, "", exit_price=exit_price, gross_return_pct=gross_return, gap_pct=gap_pct)


def _outcome(
    reason: str,
    entry_day: str,
    entry: float,
    target: float,
    stop: float,
    net_return: float,
    note: str,
    *,
    exit_price: float = 0.0,
    gross_return_pct: float = 0.0,
    gap_pct: float = 0.0,
) -> dict[str, object]:
    return {
        "entry_date": entry_day,
        "entry_price": round(entry, 2),
        "target_price": round(target, 2),
        "stop_price": round(stop, 2),
        "exit_price": round(exit_price, 2),
        "exit_reason": reason,
        "gross_return_pct": round(gross_return_pct, 3),
        "net_return_pct": round(net_return, 3),
        "entry_gap_pct": round(gap_pct, 3),
        "note": note,
    }


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.96
    phat = successes / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    half = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (center - half) * 100.0, (center + half) * 100.0


def _max_consecutive_losses(entries: pd.DataFrame) -> int:
    worst = 0
    current = 0
    for reason in entries.sort_values(["entry_date", "ticker"])["exit_reason"]:
        if reason in {"stop_loss", "ambiguous_stop_first"}:
            current += 1
            worst = max(worst, current)
        elif reason == "target_hit":
            current = 0
    return worst


def _rate(count: int, total: int) -> float:
    return count / total * 100.0 if total else 0.0


def _positive(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _fmt_day(day: str) -> str:
    text = str(day)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _limitation_note() -> str:
    return "일봉 OHLCV 기반 proxy 검증입니다. 1분봉 VWAP, 09:30 거래대금, 실시간 호가/뉴스/공시는 아직 재현하지 않습니다."
