from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


FUNDAMENTAL_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "equity",
    "total_assets",
    "per",
    "pbr",
    "dividend_yield",
]


@dataclass(frozen=True)
class FactorConfig:
    value_weight: float = 1.0
    profitability_weight: float = 1.0
    momentum_weight: float = 1.0
    low_vol_weight: float = 1.0
    min_market_cap_krw: float = 100_000_000_000
    min_avg_value_20d_krw: float = 5_000_000_000
    min_factor_groups: int = 2
    block_risk_disclosures: bool = True
    disclosure_risk_penalty: float = 25.0
    disclosure_activity_penalty: float = 2.0


def load_fundamental_snapshots(path: Path) -> pd.DataFrame:
    if not path.exists():
        return empty_fundamental_frame()
    frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str, "date": str})
    return normalize_fundamental_frame(frame)


def empty_fundamental_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["source_bas_dt", "ticker", *FUNDAMENTAL_COLUMNS])


def normalize_fundamental_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return empty_fundamental_frame()
    df = frame.copy()
    aliases = {
        "date": "source_bas_dt",
        "as_of_date": "source_bas_dt",
        "as_of_time": "source_bas_dt",
        "report_date": "source_bas_dt",
        "stock_code": "ticker",
        "code": "ticker",
        "매출액": "revenue",
        "영업이익": "operating_income",
        "operating_profit": "operating_income",
        "당기순이익": "net_income",
        "순이익": "net_income",
        "자본총계": "equity",
        "total_equity": "equity",
        "자산총계": "total_assets",
        "assets": "total_assets",
        "배당수익률": "dividend_yield",
    }
    df = df.rename(columns={key: value for key, value in aliases.items() if key in df.columns})
    if "source_bas_dt" not in df.columns:
        raise ValueError("Fundamental snapshots require source_bas_dt, date, as_of_date, as_of_time, or report_date.")
    if "ticker" not in df.columns:
        raise ValueError("Fundamental snapshots require ticker, stock_code, or code.")
    df["source_bas_dt"] = df["source_bas_dt"].astype(str).str.replace("-", "", regex=False).str[:8]
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    for column in FUNDAMENTAL_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = parse_number_series(df[column])
    keep = ["source_bas_dt", "ticker", *FUNDAMENTAL_COLUMNS]
    df = df[keep].dropna(subset=["source_bas_dt", "ticker"])
    if df.empty:
        return empty_fundamental_frame()
    grouped = df.groupby(["source_bas_dt", "ticker"], as_index=False)[FUNDAMENTAL_COLUMNS].last()
    return grouped.sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)


def merge_fundamental_snapshots(history: pd.DataFrame, fundamentals: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    df["source_bas_dt"] = df["source_bas_dt"].astype(str).str.replace("-", "", regex=False).str[:8]
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    normalized = normalize_fundamental_frame(fundamentals) if not fundamentals.empty else empty_fundamental_frame()
    if normalized.empty:
        for column in FUNDAMENTAL_COLUMNS:
            if column not in df.columns:
                df[column] = float("nan")
        df["fundamental_asof_dt"] = ""
        df["fundamental_available"] = False
        return add_fundamental_ratios(df)

    normalized = normalized.sort_values(["ticker", "source_bas_dt"]).copy()
    normalized[FUNDAMENTAL_COLUMNS] = normalized.groupby("ticker", group_keys=False)[FUNDAMENTAL_COLUMNS].ffill()
    normalized["fundamental_asof_dt"] = normalized["source_bas_dt"]
    normalized["_fundamental_date"] = pd.to_datetime(normalized["source_bas_dt"], format="%Y%m%d", errors="coerce")

    pieces: list[pd.DataFrame] = []
    for ticker, ticker_history in df.groupby("ticker", sort=False):
        hist = ticker_history.copy()
        hist["_history_date"] = pd.to_datetime(hist["source_bas_dt"], format="%Y%m%d", errors="coerce")
        fund = normalized[normalized["ticker"] == ticker].drop(columns=["ticker", "source_bas_dt"])
        if fund.empty:
            for column in FUNDAMENTAL_COLUMNS:
                hist[column] = float("nan")
            hist["fundamental_asof_dt"] = ""
            pieces.append(hist)
            continue
        merged = pd.merge_asof(
            hist.sort_values("_history_date"),
            fund.sort_values("_fundamental_date"),
            left_on="_history_date",
            right_on="_fundamental_date",
            direction="backward",
        )
        pieces.append(merged)
    result = pd.concat(pieces, ignore_index=True).drop(columns=["_history_date", "_fundamental_date"], errors="ignore")
    result["fundamental_asof_dt"] = result["fundamental_asof_dt"].fillna("")
    result["fundamental_available"] = result["fundamental_asof_dt"].astype(str).str.len() > 0
    return add_fundamental_ratios(result.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True))


def add_fundamental_ratios(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    market_cap = positive_numeric(df, "market_cap")
    revenue = positive_numeric(df, "revenue")
    operating_income = numeric(df, "operating_income")
    net_income = numeric(df, "net_income")
    equity = positive_numeric(df, "equity")
    assets = positive_numeric(df, "total_assets")
    per = positive_numeric(df, "per")
    pbr = positive_numeric(df, "pbr")

    df["earnings_yield"] = safe_divide(net_income, market_cap)
    df.loc[df["earnings_yield"].isna() & per.notna(), "earnings_yield"] = 1.0 / per[per.notna()]
    df["book_to_market"] = safe_divide(equity, market_cap)
    df.loc[df["book_to_market"].isna() & pbr.notna(), "book_to_market"] = 1.0 / pbr[pbr.notna()]
    df["sales_yield"] = safe_divide(revenue, market_cap)
    df["operating_margin"] = safe_divide(operating_income, revenue)
    df["roe"] = safe_divide(net_income, equity)
    df["roa"] = safe_divide(net_income, assets)
    if "fundamental_available" not in df.columns:
        ratio_columns = ["earnings_yield", "book_to_market", "sales_yield", "operating_margin", "roe", "roa"]
        df["fundamental_available"] = df[ratio_columns].notna().any(axis=1)
    return df


def add_rg2_price_factors(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy().sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)
    if "ret_1d_pct" not in df.columns:
        df["ret_1d_pct"] = (df["close"] / grouped["close"].shift(1) - 1.0) * 100.0
    if "avg_value_20" not in df.columns:
        df["avg_value_20"] = grouped["trading_value"].transform(lambda s: s.shift(1).rolling(20, min_periods=12).mean())
    df["close_5_ago"] = grouped["close"].shift(5)
    df["close_60_ago"] = grouped["close"].shift(60)
    df["close_120_ago"] = grouped["close"].shift(120)
    df["ret_5d_pct"] = (df["close"] / df["close_5_ago"] - 1.0) * 100.0
    df["ret_60d_pct"] = (df["close"] / df["close_60_ago"] - 1.0) * 100.0
    df["ret_120d_pct"] = (df["close"] / df["close_120_ago"] - 1.0) * 100.0
    df["volatility_60d_pct"] = grouped["ret_1d_pct"].transform(lambda s: s.rolling(60, min_periods=30).std())
    df["volatility_120d_pct"] = grouped["ret_1d_pct"].transform(lambda s: s.rolling(120, min_periods=60).std())
    return df.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def score_rg2_candidates(day_rows: pd.DataFrame, config: FactorConfig) -> pd.DataFrame:
    if day_rows.empty:
        return day_rows.copy()
    df = day_rows.copy()
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    for column in ["market_cap", "avg_value_20", "disclosure_count"]:
        if column not in df.columns:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    if "market" not in df.columns:
        df["market"] = ""
    if "disclosure_risk_flag" not in df.columns:
        df["disclosure_risk_flag"] = False
    df["disclosure_risk_flag"] = df["disclosure_risk_flag"].map(boolish)

    df["value_score"] = mean_score(
        [
            rank_score(df, "earnings_yield"),
            rank_score(df, "book_to_market"),
            rank_score(df, "sales_yield"),
            rank_score(df, "dividend_yield"),
        ],
        df.index,
    )
    df["profitability_score"] = mean_score(
        [
            rank_score(df, "operating_margin"),
            rank_score(df, "roe"),
            rank_score(df, "roa"),
        ],
        df.index,
    )
    df["momentum_score"] = mean_score(
        [
            rank_score(df, "ret_60d_pct"),
            rank_score(df, "ret_120d_pct"),
            rank_score(df, "ret_20d_pct"),
        ],
        df.index,
    )
    df["low_vol_score"] = mean_score(
        [
            rank_score(df, "volatility_60d_pct", higher_is_better=False),
            rank_score(df, "volatility_120d_pct", higher_is_better=False),
        ],
        df.index,
    )

    score_columns = ["value_score", "profitability_score", "momentum_score", "low_vol_score"]
    df["factor_group_count"] = df[score_columns].notna().sum(axis=1).astype(int)
    df["fundamental_group_count"] = df[["value_score", "profitability_score"]].notna().sum(axis=1).astype(int)
    weighted_scores = [
        ("value_score", config.value_weight),
        ("profitability_score", config.profitability_weight),
        ("momentum_score", config.momentum_weight),
        ("low_vol_score", config.low_vol_weight),
    ]
    numerator = pd.Series(0.0, index=df.index)
    denominator = pd.Series(0.0, index=df.index)
    for column, weight in weighted_scores:
        values = pd.to_numeric(df[column], errors="coerce")
        available = values.notna()
        numerator += values.fillna(0.0) * float(weight)
        denominator += available.astype(float) * float(weight)
    df["rg2_base_score"] = (numerator / denominator.replace(0, pd.NA)).astype("Float64")
    df["disclosure_penalty"] = 0.0
    df.loc[df["disclosure_risk_flag"], "disclosure_penalty"] += config.disclosure_risk_penalty
    df["disclosure_penalty"] += df["disclosure_count"].clip(lower=0, upper=5) * config.disclosure_activity_penalty
    df["rg2_composite_score"] = (df["rg2_base_score"].astype(float) - df["disclosure_penalty"]).clip(lower=0, upper=100).round(3)

    df["candidate_status"] = "pass"
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    allowed = pd.Series(True, index=df.index)
    checks = [
        (df["market"].isin(["KOSPI", "KOSDAQ"]), "unsupported_market"),
        (df["market_cap"] >= config.min_market_cap_krw, "small_market_cap"),
        (df["avg_value_20"] >= config.min_avg_value_20d_krw, "low_liquidity"),
        (df["factor_group_count"] >= int(config.min_factor_groups), "insufficient_factor_groups"),
        (pd.to_numeric(df["rg2_base_score"], errors="coerce").notna(), "missing_composite_score"),
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
    return df.sort_values(["rg2_composite_score", "ticker"], ascending=[False, True]).reset_index(drop=True)


def select_rg2_portfolio(day_rows: pd.DataFrame, config: FactorConfig, *, portfolio_size: int) -> pd.DataFrame:
    scored = score_rg2_candidates(day_rows, config)
    if scored.empty:
        return scored
    selected = scored[scored["candidate_status"] == "pass"].copy()
    if selected.empty:
        return selected
    return selected.sort_values(
        ["rg2_composite_score", "fundamental_group_count", "factor_group_count", "market_cap"],
        ascending=[False, False, False, False],
    ).head(max(int(portfolio_size), 1)).reset_index(drop=True)


def build_rebalance_signal_days(days: list[str], frequency: str) -> list[str]:
    if not days:
        return []
    frame = pd.DataFrame({"source_bas_dt": sorted(str(day).replace("-", "")[:8] for day in days)})
    frame["date"] = pd.to_datetime(frame["source_bas_dt"], format="%Y%m%d", errors="coerce")
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return []
    freq = frequency.strip().lower()
    if freq in {"m", "month", "monthly"}:
        frame["period"] = frame["date"].dt.to_period("M").astype(str)
    elif freq in {"w", "week", "weekly"}:
        frame["period"] = frame["date"].dt.to_period("W-FRI").astype(str)
    else:
        raise ValueError("frequency must be monthly or weekly")
    return frame.groupby("period")["source_bas_dt"].max().tolist()


def mean_score(scores: list[pd.Series], index: pd.Index) -> pd.Series:
    available = [score for score in scores if not score.empty]
    if not available:
        return pd.Series(pd.NA, index=index, dtype="Float64")
    frame = pd.concat(available, axis=1)
    return frame.mean(axis=1, skipna=True).astype("Float64")


def rank_score(df: pd.DataFrame, column: str, *, higher_is_better: bool = True) -> pd.Series:
    if column not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    values = pd.to_numeric(df[column], errors="coerce")
    values = values.where(values.replace([float("inf"), float("-inf")], pd.NA).notna())
    if values.notna().sum() == 0:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    ranked_values = values if higher_is_better else -values
    return (ranked_values.rank(method="average", pct=True) * 100.0).astype("Float64")


def parse_number_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(" ", "", regex=False)
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "-": pd.NA}),
        errors="coerce",
    )


def numeric(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    return pd.to_numeric(df[column], errors="coerce")


def positive_numeric(df: pd.DataFrame, column: str) -> pd.Series:
    values = numeric(df, column)
    return values.where(values > 0)


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, pd.NA)


def boolish(value: object) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "y", "yes", "risk", "위험"}
