from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ValueMomentumConfig:
    min_market_cap_krw: float = 100_000_000_000
    min_avg_value_20d_krw: float = 5_000_000_000
    value_quality_weight: float = 0.55
    momentum_weight: float = 0.45
    block_risk_disclosures: bool = True


def add_value_momentum_features(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history.copy()
    df = history.copy().sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)
    if "avg_value_20" not in df.columns and "trading_value" in df.columns:
        df["avg_value_20"] = grouped["trading_value"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).mean())
    if "ret_120d_pct" not in df.columns:
        df["close_120_ago"] = grouped["close"].shift(120)
        df["ret_120d_pct"] = (df["close"] / df["close_120_ago"] - 1.0) * 100.0
    if "ret_240d_pct" not in df.columns:
        df["close_240_ago"] = grouped["close"].shift(240)
        df["ret_240d_pct"] = (df["close"] / df["close_240_ago"] - 1.0) * 100.0
    df["market_ret_120d_median"] = df.groupby("source_bas_dt")["ret_120d_pct"].transform("median")
    df["market_ret_240d_median"] = df.groupby("source_bas_dt")["ret_240d_pct"].transform("median")
    df["relative_momentum_120d_pct"] = df["ret_120d_pct"] - df["market_ret_120d_median"]
    df["relative_momentum_240d_pct"] = df["ret_240d_pct"] - df["market_ret_240d_median"]
    return df.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def score_value_momentum_candidates(day_rows: pd.DataFrame, config: ValueMomentumConfig) -> pd.DataFrame:
    if day_rows.empty:
        return day_rows.copy()
    df = day_rows.copy()
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    for column in [
        "market_cap",
        "avg_value_20",
        "per",
        "pbr",
        "roe",
        "ret_120d_pct",
        "ret_240d_pct",
        "relative_momentum_120d_pct",
        "relative_momentum_240d_pct",
    ]:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "market" not in df.columns:
        df["market"] = ""
    if "disclosure_risk_flag" not in df.columns:
        df["disclosure_risk_flag"] = False
    df["disclosure_risk_flag"] = df["disclosure_risk_flag"].map(boolish)

    df["per_score"] = rank_score(df["per"].where(df["per"] > 0), higher_is_better=False)
    df["pbr_score"] = rank_score(df["pbr"].where(df["pbr"] > 0), higher_is_better=False)
    df["roe_score"] = rank_score(df["roe"])
    momentum_6m = df["relative_momentum_120d_pct"].where(df["relative_momentum_120d_pct"].notna(), df["ret_120d_pct"])
    momentum_12m = df["relative_momentum_240d_pct"].where(df["relative_momentum_240d_pct"].notna(), df["ret_240d_pct"])
    df["momentum_6m_score"] = rank_score(momentum_6m)
    df["momentum_12m_score"] = rank_score(momentum_12m)
    df["value_quality_score"] = mean_score([df["per_score"], df["pbr_score"], df["roe_score"]], df.index)
    df["momentum_score"] = mean_score([df["momentum_6m_score"], df["momentum_12m_score"]], df.index)

    numerator = (
        df["value_quality_score"].astype("Float64") * config.value_quality_weight
        + df["momentum_score"].astype("Float64") * config.momentum_weight
    )
    denominator = pd.Series(0.0, index=df.index)
    denominator += df["value_quality_score"].notna().astype(float) * config.value_quality_weight
    denominator += df["momentum_score"].notna().astype(float) * config.momentum_weight
    df["vm_composite_score"] = (numerator / denominator.replace(0, pd.NA)).astype("Float64").round(3)

    df["candidate_status"] = "pass"
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    allowed = pd.Series(True, index=df.index)
    checks = [
        (df["market"].isin(["KOSPI", "KOSDAQ"]), "unsupported_market"),
        (df["market_cap"] >= config.min_market_cap_krw, "small_market_cap"),
        (df["avg_value_20"] >= config.min_avg_value_20d_krw, "low_liquidity"),
        (df["per"] > 0, "invalid_per"),
        (df["pbr"] > 0, "invalid_pbr"),
        (df["roe"].notna(), "missing_roe"),
        (df["ret_120d_pct"].notna(), "missing_6m_momentum"),
        (df["vm_composite_score"].notna(), "missing_composite_score"),
    ]
    if config.block_risk_disclosures:
        checks.append((~df["disclosure_risk_flag"], "disclosure_risk"))
    index_to_pos = {idx: pos for pos, idx in enumerate(df.index)}
    for mask, reason in checks:
        failed = ~mask.fillna(False)
        allowed &= ~failed
        for idx in df.index[failed]:
            reasons[index_to_pos[idx]].append(reason)
    df.loc[~allowed, "candidate_status"] = "blocked"
    df["block_reason"] = [";".join(items) for items in reasons]
    return df.sort_values(["vm_composite_score", "ticker"], ascending=[False, True]).reset_index(drop=True)


def select_value_momentum_portfolio(day_rows: pd.DataFrame, config: ValueMomentumConfig, *, portfolio_size: int) -> pd.DataFrame:
    scored = score_value_momentum_candidates(day_rows, config)
    if scored.empty:
        return scored
    selected = scored[scored["candidate_status"] == "pass"].copy()
    if selected.empty:
        return selected
    return selected.sort_values(
        ["vm_composite_score", "value_quality_score", "momentum_score", "market_cap"],
        ascending=[False, False, False, False],
    ).head(max(int(portfolio_size), 1)).reset_index(drop=True)


def rank_score(values: pd.Series, *, higher_is_better: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.where(numeric.replace([float("inf"), float("-inf")], pd.NA).notna())
    if numeric.notna().sum() == 0:
        return pd.Series(pd.NA, index=values.index, dtype="Float64")
    ranked_values = numeric if higher_is_better else -numeric
    return (ranked_values.rank(method="average", pct=True) * 100.0).astype("Float64")


def mean_score(scores: list[pd.Series], index: pd.Index) -> pd.Series:
    available = [score for score in scores if not score.empty]
    if not available:
        return pd.Series(pd.NA, index=index, dtype="Float64")
    frame = pd.concat(available, axis=1)
    return frame.mean(axis=1, skipna=True).astype("Float64")


def boolish(value: object) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "y", "yes", "risk", "위험"}
