from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from .policy import Policy


@dataclass(frozen=True)
class IntradayResult:
    ticker: str
    date: str
    signal_time: str
    entry_time: str
    entry_price: float
    target_price: float
    stop_price: float
    exit_time: str
    exit_price: float
    exit_reason: str
    gross_return_pct: float
    net_return_pct: float
    opening_high: float
    opening_low: float
    entry_vwap: float
    note: str


def evaluate_intraday_day(bars: pd.DataFrame, policy: Policy) -> IntradayResult:
    prepared = prepare_intraday_bars(bars)
    ticker = str(prepared["ticker"].iloc[0]).zfill(6) if not prepared.empty else ""
    date = str(prepared["date"].iloc[0]) if not prepared.empty else ""
    if prepared.empty:
        return _empty_result(ticker, date, "no_bars")

    opening = prepared[(prepared["hhmm"] >= "0900") & (prepared["hhmm"] < "0910")]
    if opening.empty:
        return _empty_result(ticker, date, "missing_opening_range")
    opening_high = float(opening["high"].max())
    opening_low = float(opening["low"].min())
    if opening_high <= 0 or opening_low <= 0:
        return _empty_result(ticker, date, "bad_opening_range")
    opening_width_pct = (opening_high / opening_low - 1.0) * 100.0
    if opening_width_pct < policy.opening_range_min_pct:
        return _empty_result(ticker, date, "opening_range_too_narrow", opening_high=opening_high, opening_low=opening_low)
    if opening_width_pct > policy.opening_range_max_pct:
        return _empty_result(ticker, date, "opening_range_too_wide", opening_high=opening_high, opening_low=opening_low)

    buy_start = _compact_hhmm(policy.buy_start)
    buy_end = _compact_hhmm(policy.buy_end)
    candidates = prepared[(prepared["hhmm"] >= buy_start) & (prepared["hhmm"] <= buy_end)].copy()
    if candidates.empty:
        return _empty_result(ticker, date, "no_buy_window_bars")

    reclaim_flags = []
    opening_reclaim_level = opening_high * (1.0 - policy.vwap_reclaim_opening_high_tolerance_pct / 100.0)
    vwap_reclaim_level = 1.0 + policy.min_signal_vwap_premium_pct / 100.0
    lookback_bars = max(policy.vwap_reclaim_pullback_lookback_bars, 1)
    for idx, row in candidates.iterrows():
        prior = prepared[(prepared.index < idx) & (prepared.index >= idx - lookback_bars)]
        had_pullback = bool(((prior["close"] <= prior["vwap"]) | (prior["close"] <= opening_reclaim_level)).any()) if not prior.empty else False
        volume_ok = float(row["volume"]) >= max(float(row["volume_avg_20_prev"]) * policy.vwap_reclaim_volume_multiplier, 1.0)
        reclaim_flags.append(
            bool(
                row["close"] >= row["vwap"]
                and row["close"] >= row["vwap"] * vwap_reclaim_level
                and row["close"] >= opening_reclaim_level
                and row["trading_value_cum"] >= policy.min_intraday_trading_value_0930_krw
                and had_pullback
                and volume_ok
            )
        )
    signal = candidates[reclaim_flags]
    if signal.empty:
        return _empty_result(ticker, date, "no_vwap_reclaim_signal", opening_high=opening_high, opening_low=opening_low)

    signal_index = int(signal.index[0])
    next_rows = prepared[prepared.index > signal_index]
    if next_rows.empty:
        return _empty_result(ticker, date, "no_next_bar_for_entry", opening_high=opening_high, opening_low=opening_low)

    signal_bar = prepared.loc[signal_index]
    entry_bar = next_rows.iloc[0]
    entry_price = float(entry_bar["open"])
    if entry_price <= 0:
        return _empty_result(ticker, date, "bad_entry_price", opening_high=opening_high, opening_low=opening_low)

    target = entry_price * (1 + policy.take_profit_pct / 100.0)
    stop = entry_price * (1 - policy.stop_loss_pct / 100.0)
    exit_rows = prepared[(prepared.index >= int(entry_bar.name)) & (prepared["hhmm"] <= "1120")]
    if exit_rows.empty:
        return _empty_result(ticker, date, "no_exit_window_bars", opening_high=opening_high, opening_low=opening_low)

    for _, row in exit_rows.iterrows():
        hit_target = float(row["high"]) >= target
        hit_stop = float(row["low"]) <= stop
        if hit_target and hit_stop:
            return _priced_result(
                ticker,
                date,
                signal_bar,
                entry_bar,
                row,
                target,
                stop,
                stop,
                "ambiguous_stop_first",
                policy,
                opening_high,
                opening_low,
            )
        if hit_stop:
            return _priced_result(
                ticker,
                date,
                signal_bar,
                entry_bar,
                row,
                target,
                stop,
                stop,
                "stop_loss",
                policy,
                opening_high,
                opening_low,
            )
        if hit_target:
            return _priced_result(
                ticker,
                date,
                signal_bar,
                entry_bar,
                row,
                target,
                stop,
                target,
                "target_hit",
                policy,
                opening_high,
                opening_low,
            )

    last = exit_rows.iloc[-1]
    return _priced_result(
        ticker,
        date,
        signal_bar,
        entry_bar,
        last,
        target,
        stop,
        float(last["close"]),
        "time_exit",
        policy,
        opening_high,
        opening_low,
    )


def prepare_intraday_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return bars.copy()
    df = bars.copy()
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    df["date"] = df["date"].astype(str).str.replace("-", "", regex=False).str[:8]
    df["time"] = df["time"].astype(str).str.replace(":", "", regex=False).str.zfill(6).str[:6]
    df["hhmm"] = df["time"].str[:4]
    for column in ["open", "high", "low", "close", "volume", "trading_value"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    df = df[(df["close"] > 0) & (df["hhmm"] >= "0900") & (df["hhmm"] <= "1530")].copy()
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    df["vwap_value"] = typical * df["volume"]
    df["volume_cum"] = df.groupby(["ticker", "date"])["volume"].cumsum()
    df["vwap_value_cum"] = df.groupby(["ticker", "date"])["vwap_value"].cumsum()
    df["trading_value_cum"] = df.groupby(["ticker", "date"])["trading_value"].cumsum()
    df["vwap"] = (df["vwap_value_cum"] / df["volume_cum"].replace(0, float("nan"))).fillna(df["close"])
    df["volume_avg_20_prev"] = (
        df.groupby(["ticker", "date"])["volume"]
        .transform(lambda value: value.shift(1).rolling(20, min_periods=10).mean())
        .fillna(0.0)
    )
    return df.sort_values(["ticker", "date", "time"]).reset_index(drop=True)


def _priced_result(
    ticker: str,
    date: str,
    signal_bar: pd.Series,
    entry_bar: pd.Series,
    exit_bar: pd.Series,
    target: float,
    stop: float,
    exit_price: float,
    reason: str,
    policy: Policy,
    opening_high: float,
    opening_low: float,
) -> IntradayResult:
    entry_price = float(entry_bar["open"])
    gross = (exit_price / entry_price - 1.0) * 100.0
    net = gross - policy.backtest_round_trip_cost_default_pct
    return IntradayResult(
        ticker=ticker,
        date=date,
        signal_time=str(signal_bar["time"]),
        entry_time=str(entry_bar["time"]),
        entry_price=round(entry_price, 2),
        target_price=round(target, 2),
        stop_price=round(stop, 2),
        exit_time=str(exit_bar["time"]),
        exit_price=round(exit_price, 2),
        exit_reason=reason,
        gross_return_pct=round(gross, 3),
        net_return_pct=round(net, 3),
        opening_high=round(opening_high, 2),
        opening_low=round(opening_low, 2),
        entry_vwap=round(float(signal_bar["vwap"]), 2),
        note="",
    )


def _empty_result(
    ticker: str,
    date: str,
    note: str,
    *,
    opening_high: float = 0.0,
    opening_low: float = 0.0,
) -> IntradayResult:
    return IntradayResult(
        ticker=ticker,
        date=date,
        signal_time="",
        entry_time="",
        entry_price=0.0,
        target_price=0.0,
        stop_price=0.0,
        exit_time="",
        exit_price=0.0,
        exit_reason="no_signal",
        gross_return_pct=0.0,
        net_return_pct=0.0,
        opening_high=round(opening_high, 2),
        opening_low=round(opening_low, 2),
        entry_vwap=0.0,
        note=note,
    )


def _compact_hhmm(value: str) -> str:
    return str(value).replace(":", "")[:4]
