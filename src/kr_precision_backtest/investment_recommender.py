from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any
import warnings

import pandas as pd


@dataclass(frozen=True)
class InvestmentRecommenderConfig:
    min_market_cap_krw: float = 100_000_000_000
    min_avg_value_20d_krw: float = 5_000_000_000
    min_score_for_review: float = 62.0
    max_ret_20d_pct_before_overheat: float = 35.0
    target_pct: float = 8.0
    stop_pct: float = 4.0
    entry_pullback_pct: float = 1.5
    hold_days: int = 20
    disclosure_risk_lookback_days: int = 30
    disclosure_positive_lookback_days: int = 30
    max_price_age_calendar_days: int = 7
    allow_stale_price_data: bool = False


PRICE_NUMERIC_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trading_value",
    "market_cap",
]

FUNDAMENTAL_NUMERIC_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "equity",
    "total_assets",
]

VALUATION_NUMERIC_COLUMNS = ["per", "pbr", "dividend_yield", "bps", "eps", "dps"]

FLOW_NUMERIC_COLUMNS = [
    "foreign_net_buy_value",
    "institution_net_buy_value",
    "retail_net_buy_value",
]


def build_recommendations(
    history: pd.DataFrame,
    *,
    fundamentals: pd.DataFrame | None,
    valuation: pd.DataFrame | None,
    investor_flows: pd.DataFrame | None,
    disclosures: pd.DataFrame | None,
    universe: pd.DataFrame | None,
    config: InvestmentRecommenderConfig | None = None,
    as_of: str | None = None,
    run_date: str | None = None,
    top: int = 20,
) -> tuple[pd.DataFrame, dict[str, object]]:
    cfg = config or InvestmentRecommenderConfig()
    normalized = normalize_price_history(history)
    requested_as_of = normalize_date(as_of) if as_of else ""
    if requested_as_of:
        normalized = normalized[normalized["source_bas_dt"] <= requested_as_of].copy()
    if normalized.empty:
        summary = {
            "as_of": requested_as_of,
            "signal_day": "",
            "state": "data_unavailable",
            "recommended": 0,
            "blocked": 0,
            "watchlist": 0,
            "input_rows": 0,
        }
        return pd.DataFrame(), summary

    signal_day = str(normalized["source_bas_dt"].max())
    freshness = assess_price_data_freshness(signal_day, run_date or signal_day, cfg)
    featured = add_price_features(normalized)
    featured = attach_universe(featured, universe)
    featured = attach_fundamentals_asof(featured, fundamentals)
    featured = attach_valuation_asof(featured, valuation)
    featured = attach_flow_features(featured, investor_flows)
    day_rows = featured[featured["source_bas_dt"] == signal_day].copy()
    day_rows = attach_disclosure_context(day_rows, disclosures, signal_day, cfg)
    scored = score_day_rows(day_rows, cfg)
    if freshness["price_is_stale"] and not cfg.allow_stale_price_data:
        scored = block_stale_price_data(scored)
    recommendations = (
        scored[scored["state"].isin(["paper_review", "watchlist"])]
        .sort_values(["state_rank", "final_score", "ticker"], ascending=[True, False, True])
        .head(max(int(top), 1))
        .drop(columns=["state_rank"], errors="ignore")
        .reset_index(drop=True)
    )
    state_counts = scored["state"].value_counts().to_dict() if not scored.empty else {}
    summary = {
        "as_of": requested_as_of or signal_day,
        "signal_day": signal_day,
        "state": "paper_review" if state_counts.get("paper_review", 0) else "watchlist",
        "recommended": int(len(recommendations)),
        "paper_review": int(state_counts.get("paper_review", 0)),
        "watchlist": int(state_counts.get("watchlist", 0)),
        "blocked": int(state_counts.get("blocked", 0)),
        "input_rows": int(len(day_rows)),
        "top": int(max(int(top), 1)),
        "data_freshness": freshness,
    }
    summary["state"] = determine_summary_state(state_counts, freshness, cfg)
    return recommendations, summary


def assess_price_data_freshness(
    signal_day: str,
    run_date: str,
    config: InvestmentRecommenderConfig,
) -> dict[str, object]:
    normalized_signal_day = normalize_date(signal_day)
    normalized_run_date = normalize_date(run_date)
    signal_ts = pd.to_datetime(normalized_signal_day, format="%Y%m%d", errors="coerce")
    run_ts = pd.to_datetime(normalized_run_date, format="%Y%m%d", errors="coerce")
    if pd.isna(signal_ts) or pd.isna(run_ts):
        return {
            "run_date": normalized_run_date,
            "signal_day": normalized_signal_day,
            "price_age_calendar_days": None,
            "max_price_age_calendar_days": int(config.max_price_age_calendar_days),
            "price_is_stale": True,
            "reason": "invalid_freshness_date",
        }
    age_days = max(int((run_ts - signal_ts).days), 0)
    max_age = max(int(config.max_price_age_calendar_days), 0)
    is_stale = age_days > max_age
    return {
        "run_date": normalized_run_date,
        "signal_day": normalized_signal_day,
        "price_age_calendar_days": age_days,
        "max_price_age_calendar_days": max_age,
        "price_is_stale": bool(is_stale),
        "reason": "stale_price_data" if is_stale else "",
    }


def determine_summary_state(
    state_counts: dict[str, int],
    freshness: dict[str, object],
    config: InvestmentRecommenderConfig,
) -> str:
    if bool(freshness.get("price_is_stale")):
        if config.allow_stale_price_data:
            if state_counts.get("paper_review", 0):
                return "paper_review_stale"
            if state_counts.get("watchlist", 0):
                return "watchlist_stale"
        return "stale_data"
    if state_counts.get("paper_review", 0):
        return "paper_review"
    if state_counts.get("watchlist", 0):
        return "watchlist"
    if state_counts.get("blocked", 0):
        return "blocked"
    return "data_unavailable"


def block_stale_price_data(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty:
        return scored.copy()
    df = scored.copy()
    reason = "stale_price_data"
    existing = df["block_reason"].fillna("").astype(str) if "block_reason" in df.columns else pd.Series("", index=df.index)
    df["block_reason"] = existing.map(lambda text: append_reason(text, reason))
    df["state"] = "blocked"
    df["state_rank"] = 2
    df["paper_plan"] = "blocked; local price data is stale"
    return df


def append_reason(text: str, reason: str) -> str:
    reasons = [part for part in str(text).split(";") if part]
    if reason not in reasons:
        reasons.append(reason)
    return ";".join(reasons)


def normalize_price_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    df = history.copy()
    require_columns(df, {"ticker", "source_bas_dt", "open", "high", "low", "close"}, "price history")
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    df["source_bas_dt"] = df["source_bas_dt"].map(normalize_date)
    for column in PRICE_NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if "company" not in df.columns:
        df["company"] = ""
    if "market" not in df.columns:
        df["market"] = ""
    df = df.dropna(subset=["ticker", "source_bas_dt", "open", "high", "low", "close"])
    df["_source_date"] = pd.to_datetime(df["source_bas_dt"], format="%Y%m%d", errors="coerce")
    df = df[df["_source_date"].notna()].drop(columns=["_source_date"]).copy()
    df = df[df["close"] > 0].copy()
    return df.sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)


def add_price_features(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history.copy()
    df = history.copy().sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)
    grouped = df.groupby("ticker", group_keys=False)
    df["daily_return_pct"] = grouped["close"].pct_change(fill_method=None) * 100.0
    df["avg_value_20"] = grouped["trading_value"].transform(lambda s: s.rolling(20, min_periods=12).mean())
    df["ma_60"] = grouped["close"].transform(lambda s: s.rolling(60, min_periods=30).mean())
    df["ma_120"] = grouped["close"].transform(lambda s: s.rolling(120, min_periods=60).mean())
    df["ret_5d_pct"] = pct_change_from_shift(grouped["close"], 5)
    df["ret_20d_pct"] = pct_change_from_shift(grouped["close"], 20)
    df["ret_60d_pct"] = pct_change_from_shift(grouped["close"], 60)
    df["ret_120d_pct"] = pct_change_from_shift(grouped["close"], 120)
    df["ret_240d_pct"] = pct_change_from_shift(grouped["close"], 240)
    df["market_ret_120d_median"] = df.groupby("source_bas_dt")["ret_120d_pct"].transform("median")
    df["market_ret_240d_median"] = df.groupby("source_bas_dt")["ret_240d_pct"].transform("median")
    df["relative_momentum_120d_pct"] = df["ret_120d_pct"] - df["market_ret_120d_median"]
    df["relative_momentum_240d_pct"] = df["ret_240d_pct"] - df["market_ret_240d_median"]
    df["volatility_60d_pct"] = grouped["daily_return_pct"].transform(lambda s: s.rolling(60, min_periods=30).std())
    high_60 = grouped["close"].transform(lambda s: s.rolling(60, min_periods=30).max())
    df["drawdown_60d_pct"] = (df["close"] / high_60.replace(0, pd.NA) - 1.0) * 100.0
    df["price_vs_ma60_pct"] = (df["close"] / df["ma_60"].replace(0, pd.NA) - 1.0) * 100.0
    df["price_vs_ma120_pct"] = (df["close"] / df["ma_120"].replace(0, pd.NA) - 1.0) * 100.0
    return df.sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def attach_universe(history: pd.DataFrame, universe: pd.DataFrame | None) -> pd.DataFrame:
    df = history.copy()
    if universe is None or universe.empty:
        if "investment_universe" not in df.columns:
            df["investment_universe"] = "Y"
        return df
    uni = universe.copy()
    if "ticker" not in uni.columns:
        return df
    uni["ticker"] = uni["ticker"].astype(str).str.zfill(6)
    keep = [col for col in ["ticker", "sector", "investment_universe", "exclusion_reason"] if col in uni.columns]
    merged = df.merge(uni[keep].drop_duplicates("ticker"), on="ticker", how="left", suffixes=("", "_universe"))
    if "investment_universe" not in merged.columns:
        merged["investment_universe"] = "Y"
    merged["investment_universe"] = merged["investment_universe"].fillna("Y")
    if "sector" in merged.columns:
        merged["sector"] = merged["sector"].fillna("")
    if "exclusion_reason" in merged.columns:
        merged["exclusion_reason"] = merged["exclusion_reason"].fillna("")
    return merged


def attach_fundamentals_asof(history: pd.DataFrame, fundamentals: pd.DataFrame | None) -> pd.DataFrame:
    df = merge_asof_snapshots(
        history,
        fundamentals,
        value_columns=FUNDAMENTAL_NUMERIC_COLUMNS,
        asof_column="fundamental_asof_dt",
    )
    for column in FUNDAMENTAL_NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["roe"] = safe_divide(df["net_income"], df["equity"])
    df["roa"] = safe_divide(df["net_income"], df["total_assets"])
    df["operating_margin"] = safe_divide(df["operating_income"], df["revenue"])
    df["net_margin"] = safe_divide(df["net_income"], df["revenue"])
    df["fundamental_available"] = df["fundamental_asof_dt"].astype(str).str.len() > 0
    return df


def attach_valuation_asof(history: pd.DataFrame, valuation: pd.DataFrame | None) -> pd.DataFrame:
    df = merge_asof_snapshots(
        history,
        valuation,
        value_columns=VALUATION_NUMERIC_COLUMNS,
        asof_column="valuation_asof_dt",
    )
    for column in VALUATION_NUMERIC_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def attach_flow_features(history: pd.DataFrame, investor_flows: pd.DataFrame | None) -> pd.DataFrame:
    df = history.copy()
    if investor_flows is None or investor_flows.empty:
        df["smart_flow_5d_value"] = 0.0
        df["smart_flow_20d_value"] = 0.0
        df["smart_flow_5d_pressure_pct"] = 0.0
        df["smart_flow_20d_pressure_pct"] = 0.0
        return df
    flows = investor_flows.copy()
    require_columns(flows, {"ticker", "source_bas_dt"}, "investor flows")
    flows["ticker"] = flows["ticker"].astype(str).str.zfill(6)
    flows["source_bas_dt"] = flows["source_bas_dt"].map(normalize_date)
    for column in FLOW_NUMERIC_COLUMNS:
        if column not in flows.columns:
            flows[column] = 0.0
        flows[column] = pd.to_numeric(flows[column], errors="coerce").fillna(0.0)
    flows["smart_flow_value"] = flows["foreign_net_buy_value"] + flows["institution_net_buy_value"]
    flows = flows.sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)
    grouped = flows.groupby("ticker", group_keys=False)
    flows["smart_flow_5d_value"] = grouped["smart_flow_value"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    flows["smart_flow_20d_value"] = grouped["smart_flow_value"].transform(lambda s: s.rolling(20, min_periods=1).sum())
    keep = ["ticker", "source_bas_dt", "smart_flow_5d_value", "smart_flow_20d_value"]
    merged = df.merge(flows[keep], on=["ticker", "source_bas_dt"], how="left")
    for column in ["smart_flow_5d_value", "smart_flow_20d_value"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(0.0)
    denominator = pd.to_numeric(merged["avg_value_20"], errors="coerce").replace(0, float("nan"))
    merged["smart_flow_5d_pressure_pct"] = (
        pd.to_numeric(merged["smart_flow_5d_value"], errors="coerce") / denominator * 100.0
    ).fillna(0.0).astype(float)
    merged["smart_flow_20d_pressure_pct"] = (
        pd.to_numeric(merged["smart_flow_20d_value"], errors="coerce") / denominator * 100.0
    ).fillna(0.0).astype(float)
    return merged


def attach_disclosure_context(
    day_rows: pd.DataFrame,
    disclosures: pd.DataFrame | None,
    signal_day: str,
    config: InvestmentRecommenderConfig,
) -> pd.DataFrame:
    df = day_rows.copy()
    df["disclosure_risk_flag"] = False
    df["positive_event_flag"] = False
    df["disclosure_event_types"] = ""
    df["disclosure_titles"] = ""
    if disclosures is None or disclosures.empty:
        return df
    disc = disclosures.copy()
    if not {"ticker", "source_bas_dt"}.issubset(disc.columns):
        return df
    disc["ticker"] = disc["ticker"].astype(str).str.zfill(6)
    disc["source_bas_dt"] = disc["source_bas_dt"].map(normalize_date)
    signal_ts = pd.to_datetime(signal_day, format="%Y%m%d", errors="coerce")
    disc["_date"] = pd.to_datetime(disc["source_bas_dt"], format="%Y%m%d", errors="coerce")
    max_lookback = max(config.disclosure_risk_lookback_days, config.disclosure_positive_lookback_days)
    lower = signal_ts - pd.Timedelta(days=max_lookback)
    scoped = disc[(disc["_date"] <= signal_ts) & (disc["_date"] >= lower)].copy()
    if scoped.empty:
        return df
    if "risk_flag" not in scoped.columns:
        scoped["risk_flag"] = False
    scoped["risk_flag"] = scoped["risk_flag"].map(boolish)
    if "event_type" not in scoped.columns:
        scoped["event_type"] = ""
    if "title" not in scoped.columns:
        scoped["title"] = ""
    scoped["positive_event_flag"] = scoped.apply(classify_positive_disclosure, axis=1)
    rows = []
    for ticker, frame in scoped.groupby("ticker"):
        risk = bool(frame["risk_flag"].any())
        positive = bool(frame["positive_event_flag"].any())
        event_types = ";".join(sorted({str(v) for v in frame["event_type"].dropna().astype(str) if str(v)}))
        titles = " | ".join(str(v).strip() for v in frame["title"].dropna().astype(str).head(3))
        rows.append(
            {
                "ticker": ticker,
                "disclosure_risk_flag": risk,
                "positive_event_flag": positive,
                "disclosure_event_types": event_types,
                "disclosure_titles": titles,
            }
        )
    context = pd.DataFrame(rows)
    merged = df.drop(columns=["disclosure_risk_flag", "positive_event_flag", "disclosure_event_types", "disclosure_titles"], errors="ignore")
    merged = merged.merge(context, on="ticker", how="left")
    merged["disclosure_risk_flag"] = merged["disclosure_risk_flag"].map(boolish)
    merged["positive_event_flag"] = merged["positive_event_flag"].map(boolish)
    merged["disclosure_event_types"] = merged["disclosure_event_types"].fillna("")
    merged["disclosure_titles"] = merged["disclosure_titles"].fillna("")
    return merged


def score_day_rows(day_rows: pd.DataFrame, config: InvestmentRecommenderConfig | None = None) -> pd.DataFrame:
    cfg = config or InvestmentRecommenderConfig()
    if day_rows.empty:
        return day_rows.copy()
    df = day_rows.copy().reset_index(drop=True)
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    for column in score_numeric_columns():
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column, default in [
        ("company", ""),
        ("market", ""),
        ("investment_universe", "Y"),
        ("exclusion_reason", ""),
        ("disclosure_event_types", ""),
        ("disclosure_titles", ""),
        ("fundamental_asof_dt", ""),
        ("valuation_asof_dt", ""),
    ]:
        if column not in df.columns:
            df[column] = default
        df[column] = df[column].fillna(default)
    if "disclosure_risk_flag" not in df.columns:
        df["disclosure_risk_flag"] = False
    if "positive_event_flag" not in df.columns:
        df["positive_event_flag"] = False
    df["disclosure_risk_flag"] = df["disclosure_risk_flag"].map(boolish)
    df["positive_event_flag"] = df["positive_event_flag"].map(boolish)

    df["value_score"] = mean_score(
        [
            rank_score(df["per"].where(df["per"] > 0), higher_is_better=False),
            rank_score(df["pbr"].where(df["pbr"] > 0), higher_is_better=False),
            rank_score(df["dividend_yield"]),
        ],
        df.index,
    )
    df["quality_score"] = mean_score(
        [
            rank_score(df["roe"]),
            rank_score(df["roa"]),
            rank_score(df["operating_margin"]),
            rank_score(df["net_margin"]),
        ],
        df.index,
    )
    df["momentum_score"] = mean_score(
        [
            rank_score(df["relative_momentum_120d_pct"].where(df["relative_momentum_120d_pct"].notna(), df["ret_120d_pct"])),
            rank_score(df["relative_momentum_240d_pct"].where(df["relative_momentum_240d_pct"].notna(), df["ret_240d_pct"])),
            rank_score(df["price_vs_ma120_pct"]),
            rank_score(df["ret_60d_pct"]),
        ],
        df.index,
    )
    df["defensive_score"] = mean_score(
        [
            rank_score(df["volatility_60d_pct"], higher_is_better=False),
            rank_score(df["drawdown_60d_pct"]),
            rank_score(df["price_vs_ma120_pct"]),
        ],
        df.index,
    )
    df["flow_score"] = mean_score(
        [
            rank_score(df["smart_flow_5d_pressure_pct"]),
            rank_score(df["smart_flow_20d_pressure_pct"]),
        ],
        df.index,
    )
    df["liquidity_score"] = mean_score(
        [rank_score(df["market_cap"]), rank_score(df["avg_value_20"])],
        df.index,
    )
    df["recovery_score"] = recovery_score(df)
    df["event_score"] = pd.Series(100.0, index=df.index)
    df.loc[df["positive_event_flag"], "event_score"] = 105.0
    df.loc[df["disclosure_risk_flag"], "event_score"] = 0.0

    technique_scores = {
        "Quality Value Momentum": weighted_score(
            df,
            {"value_score": 0.30, "quality_score": 0.30, "momentum_score": 0.25, "liquidity_score": 0.15},
        ),
        "Defensive Trend Compounder": weighted_score(
            df,
            {"defensive_score": 0.30, "momentum_score": 0.25, "quality_score": 0.25, "liquidity_score": 0.20},
        ),
        "Flow-Backed Re-Rating": weighted_score(
            df,
            {"flow_score": 0.42, "momentum_score": 0.25, "value_score": 0.18, "liquidity_score": 0.15},
        ),
        "Event-Safe Recovery": weighted_score(
            df,
            {
                "recovery_score": 0.30,
                "value_score": 0.25,
                "quality_score": 0.20,
                "defensive_score": 0.15,
                "flow_score": 0.10,
            },
        ),
    }
    score_frame = pd.DataFrame(technique_scores)
    df["technique"] = score_frame.idxmax(axis=1)
    df["raw_final_score"] = score_frame.max(axis=1)
    df["overheat_penalty"] = overheat_penalty(df["ret_20d_pct"], cfg)
    df["final_score"] = (df["raw_final_score"] - df["overheat_penalty"]).clip(lower=0, upper=100).round(3)

    block_reasons = collect_block_reasons(df, cfg)
    df["block_reason"] = [";".join(reasons) for reasons in block_reasons]
    df["state"] = "watchlist"
    df.loc[df["block_reason"].astype(str).str.len() > 0, "state"] = "blocked"
    df.loc[(df["state"] != "blocked") & (df["final_score"] >= cfg.min_score_for_review), "state"] = "paper_review"
    df["state_rank"] = df["state"].map({"paper_review": 0, "watchlist": 1, "blocked": 2}).fillna(9).astype(int)
    df["score_components"] = df.apply(format_score_components, axis=1)
    df["evidence_summary"] = df.apply(format_evidence_summary, axis=1)
    df["paper_plan"] = df.apply(lambda row: format_paper_plan(row, cfg), axis=1)
    return df.sort_values(["state_rank", "final_score", "ticker"], ascending=[True, False, True]).reset_index(drop=True)


def pct_change_from_shift(grouped: pd.core.groupby.SeriesGroupBy, periods: int) -> pd.Series:
    current = grouped.obj
    shifted = grouped.shift(periods)
    return (current / shifted.replace(0, pd.NA) - 1.0) * 100.0


def merge_asof_snapshots(
    history: pd.DataFrame,
    snapshots: pd.DataFrame | None,
    *,
    value_columns: list[str],
    asof_column: str,
) -> pd.DataFrame:
    df = history.copy()
    for column in value_columns:
        if column not in df.columns:
            df[column] = pd.NA
    if asof_column not in df.columns:
        df[asof_column] = ""
    if snapshots is None or snapshots.empty or not {"ticker", "source_bas_dt"}.issubset(snapshots.columns):
        return df
    snap = snapshots.copy()
    snap["ticker"] = snap["ticker"].astype(str).str.zfill(6)
    snap["source_bas_dt"] = snap["source_bas_dt"].map(normalize_date)
    snap[asof_column] = snap["source_bas_dt"]
    snap["_snap_date"] = pd.to_datetime(snap["source_bas_dt"], format="%Y%m%d", errors="coerce")
    snap = snap[snap["_snap_date"].notna()].copy()
    for column in value_columns:
        if column not in snap.columns:
            snap[column] = pd.NA
        snap[column] = pd.to_numeric(snap[column], errors="coerce")
    hist = df.copy()
    hist["_history_date"] = pd.to_datetime(hist["source_bas_dt"], format="%Y%m%d", errors="coerce")
    hist = hist[hist["_history_date"].notna()].copy()
    pieces: list[pd.DataFrame] = []
    for ticker, ticker_history in hist.groupby("ticker", sort=False):
        ticker_snap = snap[snap["ticker"] == ticker].copy()
        if ticker_snap.empty:
            pieces.append(ticker_history)
            continue
        merged = pd.merge_asof(
            ticker_history.sort_values("_history_date").drop(columns=[asof_column] + value_columns, errors="ignore"),
            ticker_snap.sort_values("_snap_date")[["_snap_date", asof_column] + value_columns],
            left_on="_history_date",
            right_on="_snap_date",
            direction="backward",
        )
        pieces.append(merged)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = pd.concat(pieces, ignore_index=True).drop(columns=["_history_date", "_snap_date"], errors="ignore")
    result[asof_column] = result[asof_column].fillna("")
    return result.sort_values(["ticker", "source_bas_dt"]).reset_index(drop=True)


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def normalize_date(value: object) -> str:
    text = str(value).strip().replace("-", "").replace("/", "")
    return text[:8]


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.astype("Float64") / denominator.replace(0, pd.NA).astype("Float64")


def rank_score(values: pd.Series, *, higher_is_better: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.where(numeric.replace([float("inf"), float("-inf")], pd.NA).notna())
    if numeric.notna().sum() == 0:
        return pd.Series(pd.NA, index=values.index, dtype="Float64")
    ranked = numeric if higher_is_better else -numeric
    return (ranked.rank(method="average", pct=True) * 100.0).astype("Float64")


def mean_score(scores: list[pd.Series], index: pd.Index) -> pd.Series:
    available = [score for score in scores if not score.empty]
    if not available:
        return pd.Series(pd.NA, index=index, dtype="Float64")
    frame = pd.concat(available, axis=1)
    return frame.mean(axis=1, skipna=True).astype("Float64")


def weighted_score(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    numerator = pd.Series(0.0, index=frame.index, dtype="Float64")
    denominator = pd.Series(0.0, index=frame.index, dtype="Float64")
    for column, weight in weights.items():
        values = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
        present = values.notna()
        numerator += values.fillna(0.0) * weight
        denominator += present.astype(float) * weight
    return (numerator / denominator.replace(0, pd.NA)).astype("Float64")


def recovery_score(frame: pd.DataFrame) -> pd.Series:
    ret_20 = pd.to_numeric(frame["ret_20d_pct"], errors="coerce")
    ret_60 = pd.to_numeric(frame["ret_60d_pct"], errors="coerce").fillna(0.0)
    price_vs_ma120 = pd.to_numeric(frame["price_vs_ma120_pct"], errors="coerce").fillna(-100.0)
    pullback_shape = (100.0 - (ret_20.fillna(-5.0) + 5.0).abs() * 4.0).clip(lower=0, upper=100)
    trend_floor = ((ret_60 + 15.0) * 3.0).clip(lower=0, upper=100)
    ma_floor = ((price_vs_ma120 + 20.0) * 3.0).clip(lower=0, upper=100)
    return mean_score([pullback_shape.astype("Float64"), trend_floor.astype("Float64"), ma_floor.astype("Float64")], frame.index)


def overheat_penalty(ret_20d_pct: pd.Series, config: InvestmentRecommenderConfig) -> pd.Series:
    ret_20 = pd.to_numeric(ret_20d_pct, errors="coerce").fillna(0.0)
    excess = (ret_20 - config.max_ret_20d_pct_before_overheat).clip(lower=0)
    return (excess * 0.8).clip(upper=25.0)


def collect_block_reasons(df: pd.DataFrame, config: InvestmentRecommenderConfig) -> list[list[str]]:
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    checks = [
        (df["market"].isin(["KOSPI", "KOSDAQ"]), "unsupported_market"),
        (df["investment_universe"].astype(str).str.upper().ne("N"), "not_in_investment_universe"),
        (df["close"] > 0, "invalid_price"),
        (df["market_cap"] >= config.min_market_cap_krw, "small_market_cap"),
        (df["avg_value_20"] >= config.min_avg_value_20d_krw, "low_liquidity"),
        (~df["disclosure_risk_flag"], "disclosure_risk"),
        (df["final_score"].notna(), "missing_score"),
    ]
    for mask, reason in checks:
        failed = ~mask.fillna(False)
        for idx in df.index[failed]:
            reasons[int(idx)].append(reason)
    return reasons


def format_score_components(row: pd.Series) -> str:
    parts = [
        ("value", row.get("value_score")),
        ("quality", row.get("quality_score")),
        ("momentum", row.get("momentum_score")),
        ("defensive", row.get("defensive_score")),
        ("flow", row.get("flow_score")),
        ("liquidity", row.get("liquidity_score")),
    ]
    return "; ".join(f"{name}={format_number(value)}" for name, value in parts)


def format_evidence_summary(row: pd.Series) -> str:
    technique = str(row.get("technique", ""))
    score = format_number(row.get("final_score"))
    highlights = []
    if pd.notna(row.get("per")) and float(row.get("per")) > 0:
        highlights.append(f"PER {format_number(row.get('per'))}")
    if pd.notna(row.get("pbr")) and float(row.get("pbr")) > 0:
        highlights.append(f"PBR {format_number(row.get('pbr'))}")
    if pd.notna(row.get("roe")):
        highlights.append(f"ROE {format_number(float(row.get('roe')) * 100)}%")
    if pd.notna(row.get("relative_momentum_120d_pct")):
        highlights.append(f"6M rel mom {format_number(row.get('relative_momentum_120d_pct'))}%")
    if pd.notna(row.get("smart_flow_20d_pressure_pct")):
        highlights.append(f"20D smart flow {format_number(row.get('smart_flow_20d_pressure_pct'))}%")
    if boolish(row.get("positive_event_flag", False)):
        highlights.append("positive event watch")
    return f"{technique} score {score}: " + ", ".join(highlights[:6])


def format_paper_plan(row: pd.Series, config: InvestmentRecommenderConfig) -> str:
    close = pd.to_numeric(pd.Series([row.get("close")]), errors="coerce").iloc[0]
    if pd.isna(close) or close <= 0:
        return "paper only; no price plan"
    entry_low = close * (1.0 - config.entry_pullback_pct / 100.0)
    target = close * (1.0 + config.target_pct / 100.0)
    stop = close * (1.0 - config.stop_pct / 100.0)
    return (
        f"paper only; entry {entry_low:,.0f}-{close:,.0f}; "
        f"target {target:,.0f}; stop {stop:,.0f}; hold <= {config.hold_days}d"
    )


def classify_positive_disclosure(row: pd.Series) -> bool:
    text = f"{row.get('event_type', '')} {row.get('title', '')}".lower()
    positive_terms = [
        "자사주",
        "취득",
        "공급계약",
        "수주",
        "신규시설투자",
        "영업실적",
        "실적",
        "배당",
    ]
    risk_terms = ["유상증자", "전환사채", "신주인수권", "감사의견", "소송", "거래정지", "상장폐지"]
    if any(term in text for term in risk_terms):
        return False
    return any(term in text for term in positive_terms)


def score_numeric_columns() -> list[str]:
    return [
        "close",
        "market_cap",
        "avg_value_20",
        "per",
        "pbr",
        "dividend_yield",
        "roe",
        "roa",
        "operating_margin",
        "net_margin",
        "relative_momentum_120d_pct",
        "relative_momentum_240d_pct",
        "ret_20d_pct",
        "ret_60d_pct",
        "ret_120d_pct",
        "ret_240d_pct",
        "volatility_60d_pct",
        "drawdown_60d_pct",
        "price_vs_ma120_pct",
        "smart_flow_5d_pressure_pct",
        "smart_flow_20d_pressure_pct",
    ]


def boolish(value: object) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "y", "yes", "risk", "위험"}


def format_number(value: object) -> str:
    try:
        if pd.isna(value):
            return "n/a"
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        try:
            return json_ready(value.item())
        except (ValueError, TypeError):
            pass
    return value
