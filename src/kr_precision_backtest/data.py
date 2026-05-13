from __future__ import annotations

from pathlib import Path

import pandas as pd


NUMERIC_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "change",
    "day_change_pct",
    "volume",
    "trading_value",
    "listed_shares",
    "market_cap",
]


def load_price_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Price history not found: {path}")

    history = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str})
    required = {"ticker", "company", "market", "source_bas_dt", "open", "high", "low", "close", "volume", "trading_value"}
    missing = sorted(required.difference(history.columns))
    if missing:
        raise ValueError(f"Price history is missing required columns: {missing}")

    history["ticker"] = history["ticker"].astype(str).str.zfill(6)
    history["source_bas_dt"] = history["source_bas_dt"].astype(str).str.replace("-", "", regex=False)
    for column in NUMERIC_COLUMNS:
        if column in history.columns:
            history[column] = pd.to_numeric(history[column], errors="coerce")

    history = history.dropna(subset=["ticker", "source_bas_dt", "open", "high", "low", "close"])
    history = history[history["close"] > 0].copy()
    return history.sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)


def add_daily_proxy_features(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    grouped = df.groupby("ticker", group_keys=False)

    df["prev_close"] = grouped["close"].shift(1)
    df["avg_value_20"] = grouped["trading_value"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).mean())
    df["avg_volume_20"] = grouped["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).mean())
    df["high_60"] = grouped["high"].transform(lambda s: s.shift(1).rolling(60, min_periods=30).max())
    df["close_20_ago"] = grouped["close"].shift(20)
    df["ret_20d_pct"] = (df["close"] / df["close_20_ago"] - 1.0) * 100.0

    day_range = (df["high"] - df["low"]).where(df["high"] > df["low"], 0)
    df["day_range_pct"] = day_range / df["close"].replace(0, float("nan")) * 100.0
    df["close_location_pct"] = (
        (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, float("nan")) * 100.0
    ).fillna(50.0)
    df["lower_tail_recovery_pct"] = ((df["close"] / df["low"].replace(0, float("nan"))) - 1.0).fillna(0.0) * 100.0
    df["close_vs_open_pct"] = ((df["close"] / df["open"].replace(0, float("nan"))) - 1.0).fillna(0.0) * 100.0
    df["value_ratio_20"] = (df["trading_value"] / df["avg_value_20"].replace(0, float("nan"))).fillna(0.0)
    df["volume_ratio_20"] = (df["volume"] / df["avg_volume_20"].replace(0, float("nan"))).fillna(0.0)
    df["distance_from_60d_high_pct"] = (
        (df["close"] / df["high_60"].replace(0, float("nan"))) - 1.0
    ).fillna(-1.0) * 100.0

    market_median = (
        df.groupby(["source_bas_dt", "market"])["day_change_pct"]
        .median()
        .rename("market_median_change_pct")
        .reset_index()
    )
    df = df.merge(market_median, on=["source_bas_dt", "market"], how="left")
    return df.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def trading_days(history: pd.DataFrame) -> list[str]:
    return sorted(str(day) for day in history["source_bas_dt"].astype(str).unique())
