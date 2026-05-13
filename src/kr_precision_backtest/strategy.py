from __future__ import annotations

import pandas as pd

from .policy import Policy


def select_candidates(day_rows: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    if day_rows.empty:
        return day_rows.copy()

    df = day_rows.copy()
    df["block_reason"] = ""

    market_block = (
        ((df["market"] == "KOSPI") & (df["market_median_change_pct"] <= -1.2))
        | ((df["market"] == "KOSDAQ") & (df["market_median_change_pct"] <= -1.5))
    )
    extended_high_block = (df["distance_from_60d_high_pct"] >= -5.0) & (df["ret_20d_pct"] >= 15.0)

    filters = [
        (df["market"].isin(["KOSPI", "KOSDAQ"]), "unsupported_market"),
        (df["avg_value_20"] >= policy.min_avg_trading_value_20d_krw, "low_20d_trading_value"),
        (df["trading_value"] >= policy.min_intraday_trading_value_0930_krw, "low_reference_trading_value"),
        (df["value_ratio_20"] >= 1.5, "weak_volume_expansion"),
        (df["close_location_pct"] >= 55.0, "weak_close_location"),
        (df["lower_tail_recovery_pct"] >= 0.8, "weak_lower_tail_recovery"),
        (df["close_vs_open_pct"] >= -1.5, "weak_intraday_recovery"),
        (df["close_location_pct"] >= policy.min_reliable_close_location_pct, "low_reliable_close_location"),
        (df["value_ratio_20"] <= policy.max_reliable_value_ratio_20, "overheated_volume_expansion"),
        (df["distance_from_60d_high_pct"] <= policy.max_reliable_distance_from_60d_high_pct, "near_60d_high_profit_taking_risk"),
        (df["day_change_pct"] >= policy.min_reference_day_change_pct, "weak_reference_day_momentum"),
        (df["day_change_pct"] < 15.0, "reference_day_chase_risk"),
        (df["day_change_pct"] > -8.0, "reference_day_crash_risk"),
        (~market_block, "market_regime_block"),
        (~extended_high_block, "extended_high_profit_taking_risk"),
    ]

    allowed = pd.Series(True, index=df.index)
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    index_to_pos = {idx: pos for pos, idx in enumerate(df.index)}
    for mask, reason in filters:
        failed = ~mask.fillna(False)
        allowed &= ~failed
        for idx in df.index[failed]:
            reasons[index_to_pos[idx]].append(reason)

    df["is_candidate"] = allowed
    df["block_reason"] = [";".join(item) for item in reasons]
    df["signal_score"] = _score(df)

    selected = df[df["is_candidate"]].copy()
    if selected.empty:
        return selected
    return (
        selected.sort_values(
            ["signal_score", "value_ratio_20", "close_location_pct", "trading_value"],
            ascending=[False, False, False, False],
        )
        .head(policy.max_order_candidates)
        .reset_index(drop=True)
    )


def _score(df: pd.DataFrame) -> pd.Series:
    close_score = ((df["close_location_pct"] - 55.0) / 45.0 * 30.0).clip(lower=0, upper=30)
    value_score = ((df["value_ratio_20"] - 1.5) / 2.5 * 25.0).clip(lower=0, upper=25)
    tail_score = ((df["lower_tail_recovery_pct"] - 0.8) / 4.0 * 20.0).clip(lower=0, upper=20)
    market_score = ((df["market_median_change_pct"] + 1.5) / 3.0 * 15.0).clip(lower=0, upper=15)
    recovery_score = ((df["close_vs_open_pct"] + 1.5) / 5.0 * 10.0).clip(lower=0, upper=10)
    return (close_score + value_score + tail_score + market_score + recovery_score).round(2)
