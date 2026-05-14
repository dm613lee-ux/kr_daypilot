from __future__ import annotations

from pathlib import Path

import pandas as pd


OPTIONAL_NUMERIC_COLUMNS = [
    "foreign_net_buy_value",
    "institution_net_buy_value",
    "retail_net_buy_value",
    "short_sale_value_ratio",
    "short_balance_ratio",
    "credit_balance_ratio",
]


def add_eod_context_features(history: pd.DataFrame, *, context_dir: Path, universe_path: Path) -> pd.DataFrame:
    df = history.copy()
    df = add_market_regime_features(df)
    df = add_sector_features(df, universe_path)
    df = merge_optional_investor_flows(df, context_dir / "investor_flows.csv")
    df = merge_optional_short_credit(df, context_dir / "short_credit.csv")
    df = merge_optional_disclosures(df, context_dir / "disclosures.csv")
    return df.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def add_market_regime_features(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    market = (
        df.groupby(["source_bas_dt", "market"])
        .agg(
            market_advancing_ratio=("day_change_pct", lambda s: float((pd.to_numeric(s, errors="coerce") > 0).mean())),
            market_declining_ratio=("day_change_pct", lambda s: float((pd.to_numeric(s, errors="coerce") < 0).mean())),
            market_value_sum=("trading_value", "sum"),
            market_cap_sum=("market_cap", "sum"),
            market_ret_1d_median_pct=("day_change_pct", "median"),
            market_volatility_cross_section_pct=("day_change_pct", "std"),
        )
        .reset_index()
    )
    market["market_regime"] = market.apply(_market_regime, axis=1)
    return df.merge(market, on=["source_bas_dt", "market"], how="left")


def add_sector_features(history: pd.DataFrame, universe_path: Path) -> pd.DataFrame:
    df = history.copy()
    if universe_path.exists():
        universe = pd.read_csv(universe_path, dtype={"ticker": str})
        if {"ticker", "sector"}.issubset(universe.columns):
            universe = universe[["ticker", "sector"]].copy()
            universe["ticker"] = universe["ticker"].astype(str).str.zfill(6)
            universe = universe.drop_duplicates("ticker")
            df = df.merge(universe, on="ticker", how="left", suffixes=("", "_universe"))
        else:
            df["sector"] = ""
    else:
        df["sector"] = ""
    df["sector"] = df["sector"].fillna("").astype(str)
    df["sector_group"] = df["sector"].where(df["sector"].str.strip() != "", df["market"])
    df["sector_source"] = "universe"
    df.loc[df["sector_group"].isin(["KOSPI", "KOSDAQ", ""]), "sector_source"] = "market_proxy"
    sector_stats = (
        df.groupby(["source_bas_dt", "sector_group"])
        .agg(
            sector_ret_1d_median_pct=("day_change_pct", "median"),
            sector_ret_5d_median_pct=("ret_5d_pct", "median"),
            sector_advancing_ratio=("day_change_pct", lambda s: float((pd.to_numeric(s, errors="coerce") > 0).mean())),
            sector_count=("ticker", "count"),
        )
        .reset_index()
    )
    df = df.merge(sector_stats, on=["source_bas_dt", "sector_group"], how="left")
    df["sector_relative_strength_5d_pct"] = (df["ret_5d_pct"] - df["sector_ret_5d_median_pct"]).fillna(0.0)
    return df


def merge_optional_investor_flows(history: pd.DataFrame, path: Path) -> pd.DataFrame:
    df = history.copy()
    flow_columns = ["foreign_net_buy_value", "institution_net_buy_value", "retail_net_buy_value"]
    if path.exists():
        flows = read_context_csv(path)
        keep = ["source_bas_dt", "ticker"] + [col for col in flow_columns if col in flows.columns]
        flows = flows[keep].copy()
        present = [col for col in flow_columns if col in flows.columns]
        df = df.merge(flows, on=["source_bas_dt", "ticker"], how="left")
        df["investor_flow_available"] = df[present].notna().any(axis=1) if present else False
    else:
        df["investor_flow_available"] = False
    for col in flow_columns:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        df[f"{col}_z20"] = _rolling_z(df, col)
    return df


def merge_optional_short_credit(history: pd.DataFrame, path: Path) -> pd.DataFrame:
    df = history.copy()
    columns = ["short_sale_value_ratio", "short_balance_ratio", "credit_balance_ratio"]
    if path.exists():
        frame = read_context_csv(path)
        keep = ["source_bas_dt", "ticker"] + [col for col in columns if col in frame.columns]
        frame = frame[keep].copy()
        present = [col for col in columns if col in frame.columns]
        df = df.merge(frame, on=["source_bas_dt", "ticker"], how="left")
        df["short_credit_available"] = df[present].notna().any(axis=1) if present else False
    else:
        df["short_credit_available"] = False
    for col in columns:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def merge_optional_disclosures(history: pd.DataFrame, path: Path) -> pd.DataFrame:
    df = history.copy()
    if not path.exists():
        df["disclosure_count"] = 0
        df["disclosure_risk_flag"] = False
        df["disclosure_event_types"] = ""
        return df
    disclosures = read_context_csv(path)
    if disclosures.empty:
        return merge_optional_disclosures(df, Path("__missing__"))
    for column in ["event_type", "title"]:
        if column not in disclosures.columns:
            disclosures[column] = ""
    if "risk_flag" not in disclosures.columns:
        disclosures["risk_flag"] = disclosures["event_type"].astype(str).map(_disclosure_risk_flag)
    disclosures["risk_flag"] = disclosures["risk_flag"].map(_boolish)
    grouped = (
        disclosures.groupby(["source_bas_dt", "ticker"])
        .agg(
            disclosure_count=("ticker", "count"),
            disclosure_risk_flag=("risk_flag", "max"),
            disclosure_event_types=("event_type", lambda s: ";".join(sorted(set(str(x) for x in s if str(x).strip())))),
        )
        .reset_index()
    )
    df = df.merge(grouped, on=["source_bas_dt", "ticker"], how="left")
    df["disclosure_count"] = pd.to_numeric(df["disclosure_count"], errors="coerce").fillna(0).astype(int)
    df["disclosure_risk_flag"] = df["disclosure_risk_flag"].map(_boolish).fillna(False)
    df["disclosure_event_types"] = df["disclosure_event_types"].fillna("")
    return df


def read_context_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str, "date": str})
    if "source_bas_dt" not in frame.columns and "date" in frame.columns:
        frame["source_bas_dt"] = frame["date"]
    if "source_bas_dt" not in frame.columns:
        raise ValueError(f"{path} must contain source_bas_dt or date")
    if "ticker" not in frame.columns:
        raise ValueError(f"{path} must contain ticker")
    frame["source_bas_dt"] = frame["source_bas_dt"].astype(str).str.replace("-", "", regex=False).str[:8]
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    for col in OPTIONAL_NUMERIC_COLUMNS:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _rolling_z(df: pd.DataFrame, column: str) -> pd.Series:
    grouped = df.groupby("ticker", group_keys=False)[column]
    mean = grouped.transform(lambda s: s.shift(1).rolling(20, min_periods=8).mean())
    std = grouped.transform(lambda s: s.shift(1).rolling(20, min_periods=8).std())
    return ((df[column] - mean) / std.replace(0, float("nan"))).fillna(0.0)


def _market_regime(row: pd.Series) -> str:
    median = float(row.get("market_ret_1d_median_pct", 0.0) or 0.0)
    advancing = float(row.get("market_advancing_ratio", 0.0) or 0.0)
    if median <= -1.0 or advancing <= 0.35:
        return "weak"
    if median >= 1.0 or advancing >= 0.65:
        return "strong"
    return "neutral"


def _disclosure_risk_flag(value: object) -> bool:
    text = str(value).lower()
    risk_terms = [
        "유상증자",
        "전환사채",
        "cb",
        "bw",
        "감사의견",
        "불성실",
        "거래정지",
        "관리종목",
        "투자주의",
        "횡령",
        "배임",
        "소송",
    ]
    return any(term in text for term in risk_terms)


def _boolish(value: object) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "y", "yes", "risk", "위험"}
