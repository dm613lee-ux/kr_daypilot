from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .investment_recommender import (
    InvestmentRecommenderConfig,
    add_price_features,
    append_reason,
    assess_price_data_freshness,
    attach_disclosure_context,
    attach_flow_features,
    attach_fundamentals_asof,
    attach_universe,
    attach_valuation_asof,
    block_stale_price_data,
    boolish,
    determine_summary_state,
    format_number,
    format_paper_plan,
    mean_score,
    normalize_date,
    normalize_price_history,
    overheat_penalty,
    rank_score,
    require_columns,
    score_day_rows,
    weighted_score,
)


@dataclass(frozen=True)
class FundamentalCoreConfig(InvestmentRecommenderConfig):
    min_fundamental_score_for_review: float = 45.0
    min_risk_score_for_review: float = 35.0
    max_core_volatility_60d_pct: float = 8.0
    severe_trend_break_price_vs_ma120_pct: float = -20.0
    severe_trend_break_ret_60d_pct: float = -10.0


def build_core_recommendations(
    history: pd.DataFrame,
    *,
    fundamentals: pd.DataFrame | None,
    valuation: pd.DataFrame | None,
    investor_flows: pd.DataFrame | None,
    disclosures: pd.DataFrame | None,
    universe: pd.DataFrame | None,
    config: FundamentalCoreConfig | None = None,
    as_of: str | None = None,
    run_date: str | None = None,
    top: int = 20,
) -> tuple[pd.DataFrame, dict[str, object]]:
    cfg = config or FundamentalCoreConfig()
    normalized = normalize_price_history(history)
    requested_as_of = normalize_date(as_of) if as_of else ""
    if requested_as_of:
        normalized = normalized[normalized["source_bas_dt"] <= requested_as_of].copy()
    if normalized.empty:
        summary = {
            "engine": "fundamental_core",
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
    scored = score_core_day_rows(day_rows, cfg)
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
        "engine": "fundamental_core",
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
        "method": "fundamental_core_45_value_momentum_25_risk_20_event_flow_10",
    }
    summary["state"] = determine_summary_state(state_counts, freshness, cfg)
    return recommendations, summary


def score_core_day_rows(day_rows: pd.DataFrame, config: FundamentalCoreConfig | None = None) -> pd.DataFrame:
    cfg = config or FundamentalCoreConfig()
    if day_rows.empty:
        return day_rows.copy()
    require_columns(day_rows, {"ticker", "market", "close"}, "core engine day rows")

    base = score_day_rows(day_rows, InvestmentRecommenderConfig(**base_config_kwargs(cfg))).copy()
    base["fundamental_score"] = weighted_score(
        base,
        {"quality_score": 0.45, "value_score": 0.35, "liquidity_score": 0.20},
    )
    base["value_momentum_score"] = weighted_score(
        base,
        {"value_score": 0.60, "momentum_score": 0.40},
    )
    base["risk_adjustment_score"] = weighted_score(
        base,
        {"defensive_score": 0.75, "liquidity_score": 0.25},
    )
    base["event_flow_confirmation_score"] = mean_score(
        [
            rank_score(base["event_score"]),
            base["flow_score"].astype("Float64"),
        ],
        base.index,
    )
    base["technique"] = "Fundamental Core Composite"
    base["raw_final_score"] = weighted_score(
        base,
        {
            "fundamental_score": 0.45,
            "value_momentum_score": 0.25,
            "risk_adjustment_score": 0.20,
            "event_flow_confirmation_score": 0.10,
        },
    )
    base["overheat_penalty"] = overheat_penalty(base["ret_20d_pct"], cfg)
    base["final_score"] = (base["raw_final_score"] - base["overheat_penalty"]).clip(lower=0, upper=100).round(3)
    base["block_reason"] = core_block_reasons(base, cfg)
    base["state"] = "watchlist"
    base.loc[base["block_reason"].astype(str).str.len() > 0, "state"] = "blocked"
    review_mask = (
        (base["state"] != "blocked")
        & (base["final_score"] >= cfg.min_score_for_review)
        & (base["fundamental_score"] >= cfg.min_fundamental_score_for_review)
        & (base["risk_adjustment_score"] >= cfg.min_risk_score_for_review)
    )
    base.loc[review_mask, "state"] = "paper_review"
    base["state_rank"] = base["state"].map({"paper_review": 0, "watchlist": 1, "blocked": 2}).fillna(9).astype(int)
    base["score_components"] = base.apply(format_core_score_components, axis=1)
    base["evidence_summary"] = base.apply(format_core_evidence_summary, axis=1)
    base["paper_plan"] = base.apply(lambda row: format_paper_plan(row, cfg), axis=1)
    return base.sort_values(["state_rank", "final_score", "ticker"], ascending=[True, False, True]).reset_index(drop=True)


def base_config_kwargs(config: FundamentalCoreConfig) -> dict[str, object]:
    keys = InvestmentRecommenderConfig.__dataclass_fields__.keys()
    return {key: getattr(config, key) for key in keys}


def core_block_reasons(df: pd.DataFrame, config: FundamentalCoreConfig) -> list[str]:
    reasons: list[str] = []
    for _, row in df.iterrows():
        row_reasons: list[str] = []
        checks = [
            (str(row.get("market", "")) in {"KOSPI", "KOSDAQ"}, "unsupported_market"),
            (str(row.get("investment_universe", "Y")).upper() != "N", "not_in_investment_universe"),
            (to_float(row.get("close")) > 0, "invalid_price"),
            (to_float(row.get("market_cap")) >= config.min_market_cap_krw, "small_market_cap"),
            (to_float(row.get("avg_value_20")) >= config.min_avg_value_20d_krw, "low_liquidity"),
            (boolish(row.get("fundamental_available", True)), "missing_fundamentals"),
            (not boolish(row.get("disclosure_risk_flag", False)), "disclosure_risk"),
            (pd.notna(row.get("final_score")), "missing_score"),
        ]
        for ok, reason in checks:
            if not ok:
                row_reasons.append(reason)
        if is_severe_trend_break(row, config):
            row_reasons.append("core_trend_break")
        if is_core_high_volatility(row, config):
            row_reasons.append("core_high_volatility")
        reasons.append(";".join(dict.fromkeys(row_reasons)))
    return reasons


def is_severe_trend_break(row: pd.Series, config: FundamentalCoreConfig) -> bool:
    return (
        to_float(row.get("price_vs_ma120_pct")) <= config.severe_trend_break_price_vs_ma120_pct
        and to_float(row.get("ret_60d_pct")) <= config.severe_trend_break_ret_60d_pct
    )


def is_core_high_volatility(row: pd.Series, config: FundamentalCoreConfig) -> bool:
    volatility = to_float(row.get("volatility_60d_pct"))
    return volatility > config.max_core_volatility_60d_pct


def format_core_score_components(row: pd.Series) -> str:
    parts = [
        ("fundamental", row.get("fundamental_score")),
        ("value_momentum", row.get("value_momentum_score")),
        ("risk", row.get("risk_adjustment_score")),
        ("event_flow", row.get("event_flow_confirmation_score")),
        ("value", row.get("value_score")),
        ("quality", row.get("quality_score")),
        ("momentum", row.get("momentum_score")),
        ("defensive", row.get("defensive_score")),
        ("flow", row.get("flow_score")),
        ("liquidity", row.get("liquidity_score")),
    ]
    return "; ".join(f"{name}={format_number(value)}" for name, value in parts)


def format_core_evidence_summary(row: pd.Series) -> str:
    highlights = [
        f"fundamental {format_number(row.get('fundamental_score'))}",
        f"value+momentum {format_number(row.get('value_momentum_score'))}",
        f"risk {format_number(row.get('risk_adjustment_score'))}",
        f"event/flow {format_number(row.get('event_flow_confirmation_score'))}",
    ]
    if pd.notna(row.get("per")) and to_float(row.get("per")) > 0:
        highlights.append(f"PER {format_number(row.get('per'))}")
    if pd.notna(row.get("pbr")) and to_float(row.get("pbr")) > 0:
        highlights.append(f"PBR {format_number(row.get('pbr'))}")
    if pd.notna(row.get("roe")):
        highlights.append(f"ROE {format_number(to_float(row.get('roe')) * 100)}%")
    return f"Fundamental Core score {format_number(row.get('final_score'))}: " + ", ".join(highlights)


def to_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number
