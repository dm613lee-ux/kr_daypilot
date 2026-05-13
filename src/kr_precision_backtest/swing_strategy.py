from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import pandas as pd

from .policy import Policy


MIN_MARKET_CAP_KRW = 100_000_000_000
MIN_REFERENCE_VALUE_KRW = 5_000_000_000


@dataclass(frozen=True)
class PaperSizing:
    quantity: int
    max_loss_krw: float
    position_value_krw: float
    risk_budget_krw: float


def add_swing_features(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    grouped = df.groupby("ticker", group_keys=False)
    df["ret_1d_pct"] = df["day_change_pct"].fillna((df["close"] / grouped["close"].shift(1) - 1.0) * 100.0)
    df["ret_2d_pct"] = (df["close"] / grouped["close"].shift(2) - 1.0) * 100.0
    df["ret_5d_pct"] = (df["close"] / grouped["close"].shift(5) - 1.0) * 100.0
    df["turnover_pct"] = (df["volume"] / df["listed_shares"].replace(0, float("nan")) * 100.0).fillna(0.0)
    value_std_20 = grouped["trading_value"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).std())
    turnover_mean_20 = grouped["turnover_pct"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).mean())
    turnover_std_20 = grouped["turnover_pct"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).std())
    volatility_20 = grouped["ret_1d_pct"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).std())
    df["trading_value_z_20"] = ((df["trading_value"] - df["avg_value_20"]) / value_std_20.replace(0, float("nan"))).fillna(0.0)
    df["turnover_z_20"] = ((df["turnover_pct"] - turnover_mean_20) / turnover_std_20.replace(0, float("nan"))).fillna(0.0)
    df["volatility_20d_pct"] = volatility_20.fillna(0.0)

    market_ret_5d = (
        df.groupby(["source_bas_dt", "market"])["ret_5d_pct"]
        .median()
        .rename("market_ret_5d_median_pct")
        .reset_index()
    )
    df = df.merge(market_ret_5d, on=["source_bas_dt", "market"], how="left")
    df["relative_strength_5d_pct"] = (df["ret_5d_pct"] - df["market_ret_5d_median_pct"]).fillna(0.0)
    return df.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def score_swing_candidates(day_rows: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    if day_rows.empty:
        return day_rows.copy()
    df = day_rows.copy()
    numeric_columns = [
        "ret_1d_pct",
        "ret_2d_pct",
        "ret_5d_pct",
        "value_ratio_20",
        "trading_value_z_20",
        "turnover_z_20",
        "close_location_pct",
        "lower_tail_recovery_pct",
        "relative_strength_5d_pct",
        "volatility_20d_pct",
        "market_median_change_pct",
        "distance_from_60d_high_pct",
        "ret_20d_pct",
        "market_cap",
        "avg_value_20",
        "trading_value",
        "market_advancing_ratio",
        "market_ret_1d_median_pct",
        "market_volatility_cross_section_pct",
        "sector_relative_strength_5d_pct",
        "sector_advancing_ratio",
        "foreign_net_buy_value_z20",
        "institution_net_buy_value_z20",
        "retail_net_buy_value_z20",
        "short_sale_value_ratio",
        "credit_balance_ratio",
        "disclosure_count",
    ]
    for column in numeric_columns:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    if "market_regime" not in df.columns:
        df["market_regime"] = "neutral"
    if "sector_group" not in df.columns:
        df["sector_group"] = df.get("market", "")
    if "sector_source" not in df.columns:
        df["sector_source"] = "missing"
    for column in ["investor_flow_available", "short_credit_available", "disclosure_risk_flag"]:
        if column not in df.columns:
            df[column] = False
        df[column] = df[column].fillna(False).astype(bool)

    reversal_score = (((-df["ret_5d_pct"]) - 1.0) / 9.0 * 28.0).clip(lower=0, upper=28)
    short_reversal_score = (((-df["ret_2d_pct"]) - 0.3) / 4.5 * 12.0).clip(lower=0, upper=12)
    value_score = ((df["value_ratio_20"] - 0.9) / 2.6 * 18.0).clip(lower=0, upper=18)
    value_z_score = ((df["trading_value_z_20"] + 0.2) / 2.8 * 10.0).clip(lower=0, upper=10)
    close_score = ((df["close_location_pct"] - 35.0) / 50.0 * 14.0).clip(lower=0, upper=14)
    tail_score = (df["lower_tail_recovery_pct"] / 4.0 * 8.0).clip(lower=0, upper=8)
    relative_score = ((df["relative_strength_5d_pct"] + 4.0) / 10.0 * 6.0).clip(lower=0, upper=6)
    market_score = ((df["market_median_change_pct"] + 1.5) / 3.0 * 4.0).clip(lower=0, upper=4)
    volatility_penalty = ((df["volatility_20d_pct"] - 6.0) / 4.0 * 8.0).clip(lower=0, upper=8)

    df["alpha_score"] = (
        reversal_score
        + short_reversal_score
        + value_score
        + value_z_score
        + close_score
        + tail_score
        + relative_score
        + market_score
        - volatility_penalty
    ).round(2)

    df["candidate_status"] = "pass"
    df["block_reason"] = ""
    checks = [
        (df["market"].isin(["KOSPI", "KOSDAQ"]), "unsupported_market"),
        (df["avg_value_20"] >= policy.min_avg_trading_value_20d_krw, "low_20d_trading_value"),
        (df["trading_value"] >= MIN_REFERENCE_VALUE_KRW, "low_reference_trading_value"),
        (df["market_cap"] >= MIN_MARKET_CAP_KRW, "small_market_cap_capacity_risk"),
        (df["close"] >= 1000, "low_price_noise_risk"),
        (df["ret_5d_pct"] >= -25.0, "falling_knife_5d"),
        (df["ret_5d_pct"] <= 12.0, "extended_5d_chase_risk"),
        (df["ret_1d_pct"] > -12.0, "reference_day_crash_risk"),
        (df["ret_1d_pct"] < 12.0, "reference_day_chase_risk"),
        (df["value_ratio_20"] >= 1.0, "no_volume_shock"),
        (df["value_ratio_20"] <= 8.0, "extreme_volume_event_risk"),
        (df["close_location_pct"] >= 35.0, "weak_close_location"),
        (~((df["distance_from_60d_high_pct"] >= -3.0) & (df["ret_20d_pct"] >= 20.0)), "extended_high_profit_taking_risk"),
    ]
    allowed = pd.Series(True, index=df.index)
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    index_to_pos = {idx: pos for pos, idx in enumerate(df.index)}
    for mask, reason in checks:
        failed = ~mask.fillna(False)
        allowed &= ~failed
        for idx in df.index[failed]:
            reasons[index_to_pos[idx]].append(reason)
    df.loc[~allowed, "candidate_status"] = "blocked"
    df["block_reason"] = [";".join(item) for item in reasons]
    return df


def select_swing_candidates(day_rows: pd.DataFrame, policy: Policy, *, max_candidates: int) -> pd.DataFrame:
    scored = score_swing_candidates(day_rows, policy)
    if scored.empty:
        return scored
    selected = scored[scored["candidate_status"] == "pass"].copy()
    if selected.empty:
        return selected
    sort_columns = ["alpha_score", "trading_value_z_20", "value_ratio_20", "trading_value"]
    return selected.sort_values(sort_columns, ascending=[False, False, False, False]).head(max_candidates).reset_index(drop=True)


def build_paper_order(candidate: dict[str, object], policy: Policy, *, rank: int) -> dict[str, object]:
    reference_close = _positive(candidate.get("close"))
    entry_discount = float(getattr(policy, "swing_entry_discount_pct", 0.5))
    take_profit = float(getattr(policy, "swing_take_profit_pct", 3.0))
    stop_loss = float(getattr(policy, "swing_stop_loss_pct", 2.0))
    entry_price = reference_close * (1.0 - entry_discount / 100.0)
    target_price = entry_price * (1.0 + take_profit / 100.0)
    stop_price = entry_price * (1.0 - stop_loss / 100.0)
    sizing = size_order(entry_price, stop_price, policy)
    return {
        "paper_order_rank": rank,
        "ticker": str(candidate.get("ticker", "")).zfill(6),
        "company": candidate.get("company", ""),
        "market": candidate.get("market", ""),
        "reference_day": _display_day(candidate.get("source_bas_dt", "")),
        "reference_close": round(reference_close, 2),
        "alpha_score": round(float(candidate.get("alpha_score", 0.0)), 2),
        "entry_limit_price": round(entry_price, 2),
        "target_price": round(target_price, 2),
        "stop_price": round(stop_price, 2),
        "quantity": sizing.quantity,
        "planned_position_value_krw": round(sizing.position_value_krw, 0),
        "planned_max_loss_krw": round(sizing.max_loss_krw, 0),
        "risk_budget_krw": round(sizing.risk_budget_krw, 0),
        "cancel_time": "09:20",
        "holding_period": f"D+1~D+{int(getattr(policy, 'swing_hold_days', 3))}",
        "entry_rule": f"다음 거래일 {entry_discount:.1f}% 하단 지정가, 미체결 시 폐기",
        "risk_rule": "목표/손절/3거래일 시간청산 중 먼저 발생한 조건으로 종료",
        "feature_summary": _feature_summary(candidate),
        "ret_1d_pct": round(float(candidate.get("ret_1d_pct", 0.0)), 3),
        "ret_5d_pct": round(float(candidate.get("ret_5d_pct", 0.0)), 3),
        "value_ratio_20": round(float(candidate.get("value_ratio_20", 0.0)), 3),
        "trading_value_z_20": round(float(candidate.get("trading_value_z_20", 0.0)), 3),
        "close_location_pct": round(float(candidate.get("close_location_pct", 0.0)), 3),
        "relative_strength_5d_pct": round(float(candidate.get("relative_strength_5d_pct", 0.0)), 3),
        "market_regime": str(candidate.get("market_regime", "")),
        "market_advancing_ratio": round(_number(candidate.get("market_advancing_ratio")), 3),
        "market_ret_1d_median_pct": round(_number(candidate.get("market_ret_1d_median_pct")), 3),
        "sector_group": str(candidate.get("sector_group", "")),
        "sector_source": str(candidate.get("sector_source", "")),
        "sector_relative_strength_5d_pct": round(_number(candidate.get("sector_relative_strength_5d_pct")), 3),
        "investor_flow_available": bool(candidate.get("investor_flow_available", False)),
        "foreign_net_buy_value_z20": round(_number(candidate.get("foreign_net_buy_value_z20")), 3),
        "institution_net_buy_value_z20": round(_number(candidate.get("institution_net_buy_value_z20")), 3),
        "retail_net_buy_value_z20": round(_number(candidate.get("retail_net_buy_value_z20")), 3),
        "short_credit_available": bool(candidate.get("short_credit_available", False)),
        "short_sale_value_ratio": round(_number(candidate.get("short_sale_value_ratio")), 3),
        "credit_balance_ratio": round(_number(candidate.get("credit_balance_ratio")), 3),
        "disclosure_count": int(_number(candidate.get("disclosure_count"))),
        "disclosure_risk_flag": bool(candidate.get("disclosure_risk_flag", False)),
        "disclosure_event_types": str(candidate.get("disclosure_event_types", "")),
        "paper_status": "ready" if sizing.quantity > 0 else "blocked",
        "paper_block_reason": "" if sizing.quantity > 0 else "risk_budget_or_position_limit_too_small",
    }


def size_order(entry_price: float, stop_price: float, policy: Policy) -> PaperSizing:
    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return PaperSizing(0, 0.0, 0.0, 0.0)
    max_position = float(getattr(policy, "max_position_value_krw", 300_000))
    daily_loss = float(getattr(policy, "max_daily_loss_krw", 30_000))
    max_orders = max(int(getattr(policy, "max_order_candidates", 2)), 1)
    risk_budget = daily_loss / max_orders
    per_share_loss = entry_price - stop_price
    quantity_by_risk = math.floor(risk_budget / per_share_loss)
    quantity_by_position = math.floor(max_position / entry_price)
    quantity = max(min(quantity_by_risk, quantity_by_position), 0)
    position_value = quantity * entry_price
    max_loss = quantity * per_share_loss
    return PaperSizing(quantity, max_loss, position_value, risk_budget)


def simulate_swing_trade(
    candidate: dict[str, object],
    ticker_rows: pd.DataFrame,
    reference_day: str,
    policy: Policy,
    *,
    order_rank: int,
) -> dict[str, object]:
    order = build_paper_order(candidate, policy, rank=order_rank)
    hold_days = max(int(getattr(policy, "swing_hold_days", 3)), 1)
    future = ticker_rows[ticker_rows["source_bas_dt"].astype(str) > reference_day].sort_values("source_bas_dt").head(hold_days)
    if future.empty:
        return {**order, **_empty_label("no_future_data")}
    entry_day = future.iloc[0]
    entry_open = _positive(entry_day.get("open"))
    reference_close = _positive(candidate.get("close"))
    entry_limit = float(order["entry_limit_price"])
    if reference_close <= 0 or entry_open <= 0:
        return {**order, **_empty_label("bad_price_data")}

    gap_pct = (entry_open / reference_close - 1.0) * 100.0
    min_gap = float(getattr(policy, "swing_min_open_gap_pct", 0.5))
    max_gap = float(getattr(policy, "swing_max_open_gap_pct", 4.0))
    max_down_gap = float(getattr(policy, "swing_max_down_gap_pct", -4.0))
    if gap_pct < max_down_gap:
        return {**order, **_no_fill_label(f"gap_down_execution_block:{gap_pct:.2f}", future, entry_limit, gap_pct)}
    if gap_pct < min_gap:
        return {**order, **_no_fill_label(f"morning_confirmation_missing:{gap_pct:.2f}", future, entry_limit, gap_pct)}
    if gap_pct > max_gap:
        return {**order, **_no_fill_label(f"gap_up_execution_block:{gap_pct:.2f}", future, entry_limit, gap_pct)}

    entry_low = _positive(entry_day.get("low"))
    entry_high = _positive(entry_day.get("high"))
    if entry_low <= 0 or entry_high <= 0 or not (entry_low <= entry_limit <= max(entry_high, entry_open)):
        return {**order, **_no_fill_label("limit_not_touched", future, entry_limit, gap_pct)}

    entry_price = entry_limit
    target = float(order["target_price"])
    stop = float(order["stop_price"])
    max_adverse = 0.0
    max_favorable = 0.0
    exit_day = _display_day(future.iloc[-1].get("source_bas_dt", ""))
    exit_price = _positive(future.iloc[-1].get("close"))
    exit_reason = "time_exit"

    for _, bar in future.iterrows():
        high = _positive(bar.get("high"))
        low = _positive(bar.get("low"))
        close = _positive(bar.get("close"))
        if high <= 0 or low <= 0 or close <= 0:
            continue
        max_adverse = min(max_adverse, (low / entry_price - 1.0) * 100.0)
        max_favorable = max(max_favorable, (high / entry_price - 1.0) * 100.0)
        hit_target = high >= target
        hit_stop = low <= stop
        exit_day = _display_day(bar.get("source_bas_dt", ""))
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

    gross = (exit_price / entry_price - 1.0) * 100.0
    net = gross - policy.backtest_round_trip_cost_default_pct
    return {
        **order,
        "entry_date": _display_day(entry_day.get("source_bas_dt", "")),
        "paper_filled": True,
        "fill_price": round(entry_price, 2),
        "entry_gap_pct": round(gap_pct, 3),
        "exit_date": exit_day,
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "target_hit_d1_d3": exit_reason == "target_hit",
        "stop_hit_d1_d3": exit_reason in {"stop_hit", "ambiguous_stop_first"},
        "time_exit": exit_reason == "time_exit",
        "max_adverse_excursion_pct": round(max_adverse, 3),
        "max_favorable_excursion_pct": round(max_favorable, 3),
        "gross_return_pct": round(gross, 3),
        "net_return_after_cost_pct": round(net, 3),
        "missed_max_return_pct": 0.0,
        "label_note": "",
    }


def summarize_swing_results(results: pd.DataFrame, policy: Policy) -> dict[str, object]:
    if results.empty:
        return _empty_summary(policy, "no results")
    order_results = results[results["paper_order_rank"] <= policy.max_order_candidates].copy()
    filled = order_results[order_results["paper_filled"] == True].copy()  # noqa: E712
    target_hits = int((filled["target_hit_d1_d3"] == True).sum()) if not filled.empty else 0  # noqa: E712
    stop_hits = int((filled["stop_hit_d1_d3"] == True).sum()) if not filled.empty else 0  # noqa: E712
    time_exits = int((filled["time_exit"] == True).sum()) if not filled.empty else 0  # noqa: E712
    no_fills = int((order_results["paper_filled"] == False).sum()) if not order_results.empty else 0  # noqa: E712
    wilson_low, wilson_high = _wilson(target_hits, len(filled))
    avg_net = float(filled["net_return_after_cost_pct"].mean()) if not filled.empty else 0.0
    median_net = float(filled["net_return_after_cost_pct"].median()) if not filled.empty else 0.0
    research_pass = (
        len(filled) >= policy.oos_min_trades
        and _pct(target_hits, len(filled)) >= policy.research_pass_success_rate_pct
        and wilson_low >= policy.research_wilson_lower_pct
        and avg_net > 0
    )
    return {
        "strategy": "1~3일 스윙 페이퍼 주문 플랜",
        "recommendations": int(len(results)),
        "paper_orders": int(len(order_results)),
        "paper_filled": int(len(filled)),
        "no_fills": no_fills,
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "time_exits": time_exits,
        "fill_rate": round(_pct(len(filled), len(order_results)), 2),
        "target_success_rate": round(_pct(target_hits, len(filled)), 2),
        "stop_rate": round(_pct(stop_hits, len(filled)), 2),
        "time_exit_rate": round(_pct(time_exits, len(filled)), 2),
        "wilson_low": round(wilson_low, 2),
        "wilson_high": round(wilson_high, 2),
        "avg_net_return_after_cost_pct": round(avg_net, 3),
        "median_net_return_after_cost_pct": round(median_net, 3),
        "max_consecutive_stops": _max_consecutive_stops(filled),
        "research_pass": bool(research_pass),
        "required_oos_trades": policy.oos_min_trades,
        "required_success_rate": policy.research_pass_success_rate_pct,
        "required_wilson_low": policy.research_wilson_lower_pct,
        "note": "실전 주문이 아니라 페이퍼 주문 플랜 검증입니다.",
    }


def _empty_summary(policy: Policy, note: str) -> dict[str, object]:
    return {
        "strategy": "1~3일 스윙 페이퍼 주문 플랜",
        "recommendations": 0,
        "paper_orders": 0,
        "paper_filled": 0,
        "no_fills": 0,
        "target_hits": 0,
        "stop_hits": 0,
        "time_exits": 0,
        "fill_rate": 0.0,
        "target_success_rate": 0.0,
        "stop_rate": 0.0,
        "time_exit_rate": 0.0,
        "wilson_low": 0.0,
        "wilson_high": 0.0,
        "avg_net_return_after_cost_pct": 0.0,
        "median_net_return_after_cost_pct": 0.0,
        "max_consecutive_stops": 0,
        "research_pass": False,
        "required_oos_trades": policy.oos_min_trades,
        "required_success_rate": policy.research_pass_success_rate_pct,
        "required_wilson_low": policy.research_wilson_lower_pct,
        "note": note,
    }


def _empty_label(note: str) -> dict[str, object]:
    return {
        "entry_date": "",
        "paper_filled": False,
        "fill_price": 0.0,
        "entry_gap_pct": 0.0,
        "exit_date": "",
        "exit_price": 0.0,
        "exit_reason": "no_future_data",
        "target_hit_d1_d3": False,
        "stop_hit_d1_d3": False,
        "time_exit": False,
        "max_adverse_excursion_pct": 0.0,
        "max_favorable_excursion_pct": 0.0,
        "gross_return_pct": 0.0,
        "net_return_after_cost_pct": 0.0,
        "missed_max_return_pct": 0.0,
        "label_note": note,
    }


def _no_fill_label(note: str, future: pd.DataFrame, entry_limit: float, gap_pct: float) -> dict[str, object]:
    max_high = float(pd.to_numeric(future["high"], errors="coerce").max()) if not future.empty else 0.0
    missed = (max_high / entry_limit - 1.0) * 100.0 if entry_limit > 0 and max_high > 0 else 0.0
    return {
        "entry_date": _display_day(future.iloc[0].get("source_bas_dt", "")) if not future.empty else "",
        "paper_filled": False,
        "fill_price": 0.0,
        "entry_gap_pct": round(gap_pct, 3),
        "exit_date": "",
        "exit_price": 0.0,
        "exit_reason": "no_fill",
        "target_hit_d1_d3": False,
        "stop_hit_d1_d3": False,
        "time_exit": False,
        "max_adverse_excursion_pct": 0.0,
        "max_favorable_excursion_pct": 0.0,
        "gross_return_pct": 0.0,
        "net_return_after_cost_pct": 0.0,
        "missed_max_return_pct": round(missed, 3),
        "label_note": note,
    }


def _feature_summary(candidate: dict[str, object]) -> str:
    market_regime = str(candidate.get("market_regime", "neutral") or "neutral")
    sector_rs = _number(candidate.get("sector_relative_strength_5d_pct"))
    return (
        f"5일 {float(candidate.get('ret_5d_pct', 0.0)):.1f}%, "
        f"거래대금 {float(candidate.get('value_ratio_20', 0.0)):.1f}배, "
        f"종가위치 {float(candidate.get('close_location_pct', 0.0)):.0f}점, "
        f"시장국면 {market_regime}, 섹터대비 {sector_rs:.1f}%p"
    )


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.96
    phat = successes / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    half = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (center - half) * 100.0, (center + half) * 100.0


def _max_consecutive_stops(filled: pd.DataFrame) -> int:
    worst = 0
    current = 0
    if filled.empty:
        return 0
    for is_stop in filled.sort_values(["entry_date", "paper_order_rank"])["stop_hit_d1_d3"]:
        if bool(is_stop):
            current += 1
            worst = max(worst, current)
        else:
            current = 0
    return worst


def _pct(count: int, total: int) -> float:
    return count / total * 100.0 if total else 0.0


def _positive(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _display_day(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value)


def output_columns() -> list[str]:
    return [
        "reference_day",
        "entry_date",
        "paper_order_rank",
        "ticker",
        "company",
        "market",
        "alpha_score",
        "reference_close",
        "entry_limit_price",
        "target_price",
        "stop_price",
        "quantity",
        "planned_max_loss_krw",
        "paper_filled",
        "exit_reason",
        "target_hit_d1_d3",
        "stop_hit_d1_d3",
        "time_exit",
        "max_adverse_excursion_pct",
        "net_return_after_cost_pct",
        "label_note",
        "feature_summary",
        "ret_1d_pct",
        "ret_5d_pct",
        "value_ratio_20",
        "trading_value_z_20",
        "close_location_pct",
        "relative_strength_5d_pct",
        "market_regime",
        "market_advancing_ratio",
        "market_ret_1d_median_pct",
        "sector_group",
        "sector_source",
        "sector_relative_strength_5d_pct",
        "investor_flow_available",
        "foreign_net_buy_value_z20",
        "institution_net_buy_value_z20",
        "retail_net_buy_value_z20",
        "short_credit_available",
        "short_sale_value_ratio",
        "credit_balance_ratio",
        "disclosure_count",
        "disclosure_risk_flag",
        "disclosure_event_types",
    ]
