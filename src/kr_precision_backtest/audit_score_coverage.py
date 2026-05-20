from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .fundamental_core_engine import FundamentalCoreConfig, score_core_day_rows
from .investment_recommender import (
    InvestmentRecommenderConfig,
    add_price_features,
    attach_disclosure_context,
    attach_flow_features,
    attach_fundamentals_asof,
    attach_universe,
    attach_valuation_asof,
    json_ready,
    normalize_date,
    normalize_price_history,
    score_day_rows,
)
from .run_investment_recommender import (
    DEFAULT_DISCLOSURES,
    DEFAULT_FUNDAMENTALS,
    DEFAULT_INVESTOR_FLOWS,
    DEFAULT_PRICE_HISTORY,
    DEFAULT_UNIVERSE,
    DEFAULT_VALUATION,
    load_optional_csv,
    load_required_csv,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "score_coverage_audit"


SCORE_INPUT_FLAGS = {
    "value_score": ["per_valid", "pbr_valid", "dividend_yield_present"],
    "quality_score": ["roe_present", "roa_present", "operating_margin_present", "net_margin_present"],
    "momentum_score": ["momentum_120_present", "momentum_240_present", "price_vs_ma120_present", "ret_60d_present"],
    "defensive_score": ["volatility_60d_present", "drawdown_60d_present", "price_vs_ma120_present"],
    "flow_score": ["smart_flow_asof_present"],
    "liquidity_score": ["market_cap_present", "avg_value_20_present"],
    "recovery_score": ["ret_20d_present", "ret_60d_present", "price_vs_ma120_present"],
    "event_score": ["event_context_present"],
    "fundamental_score": [
        "per_valid",
        "pbr_valid",
        "dividend_yield_present",
        "roe_present",
        "roa_present",
        "operating_margin_present",
        "net_margin_present",
        "market_cap_present",
        "avg_value_20_present",
    ],
    "value_momentum_score": [
        "per_valid",
        "pbr_valid",
        "dividend_yield_present",
        "momentum_120_present",
        "momentum_240_present",
        "price_vs_ma120_present",
        "ret_60d_present",
    ],
    "risk_adjustment_score": [
        "volatility_60d_present",
        "drawdown_60d_present",
        "price_vs_ma120_present",
        "market_cap_present",
        "avg_value_20_present",
    ],
    "event_flow_confirmation_score": ["event_context_present", "smart_flow_asof_present"],
    "raw_final_score": ["raw_final_inputs_present"],
    "final_score": ["raw_final_inputs_present"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit KR DayPilot score column coverage and neutral fallback usage.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--valuation", type=Path, default=DEFAULT_VALUATION)
    parser.add_argument("--investor-flows", type=Path, default=DEFAULT_INVESTOR_FLOWS)
    parser.add_argument("--disclosures", type=Path, default=DEFAULT_DISCLOSURES)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", default="", help="Signal date in YYYYMMDD. Defaults to latest local price date.")
    args = parser.parse_args()

    audit = build_score_coverage_audit(
        load_required_csv(args.price_history),
        fundamentals=load_optional_csv(args.fundamentals),
        valuation=load_optional_csv(args.valuation),
        investor_flows=load_optional_csv(args.investor_flows),
        disclosures=load_optional_csv(args.disclosures),
        universe=load_optional_csv(args.universe),
        as_of=args.as_of or None,
    )
    paths = write_audit_outputs(audit, args.output)
    print("KR DayPilot score coverage audit complete.")
    print(f"Signal day: {audit['summary']['signal_day']}")
    print(f"Universe rows: {audit['summary']['universe_rows']}")
    print(f"High risk columns: {audit['summary']['high_risk_columns']}")
    print(f"Markdown: {paths['latest_md']}")
    return 0


def build_score_coverage_audit(
    history: pd.DataFrame,
    *,
    fundamentals: pd.DataFrame | None,
    valuation: pd.DataFrame | None,
    investor_flows: pd.DataFrame | None,
    disclosures: pd.DataFrame | None,
    universe: pd.DataFrame | None,
    as_of: str | None = None,
) -> dict[str, object]:
    day_rows, signal_day = prepare_day_rows(
        history,
        fundamentals=fundamentals,
        valuation=valuation,
        investor_flows=investor_flows,
        disclosures=disclosures,
        universe=universe,
        as_of=as_of,
    )
    tactical = score_day_rows(day_rows, InvestmentRecommenderConfig())
    core = score_core_day_rows(day_rows, FundamentalCoreConfig())
    flags = add_validity_flags(day_rows, tactical, core)
    tactical_rows = audit_score_columns(tactical, flags, "tactical", ["raw_final_score", "final_score"])
    base_rows = audit_score_columns(
        tactical,
        flags,
        "shared",
        ["value_score", "quality_score", "momentum_score", "defensive_score", "flow_score", "liquidity_score", "recovery_score", "event_score"],
    )
    core_rows = audit_score_columns(
        core,
        flags,
        "core",
        ["fundamental_score", "value_momentum_score", "risk_adjustment_score", "event_flow_confirmation_score", "raw_final_score", "final_score"],
    )
    raw_rows = audit_raw_inputs(day_rows, flags)
    rows = base_rows + tactical_rows + core_rows
    high_risk = [
        row["score_column"]
        for row in rows
        if row["raw_full_pct"] < 70.0 or row["fallback_neutral_pct"] >= 50.0 or row["score_coverage_pct"] < 95.0
    ]
    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "summary": {
            "signal_day": signal_day,
            "universe_rows": int(len(day_rows)),
            "high_risk_columns": sorted(set(high_risk)),
            "flow_asof_coverage_pct": pct(flags["smart_flow_asof_present"].sum(), len(flags)),
            "event_active_pct": pct(flags["event_context_present"].sum(), len(flags)),
            "fundamental_available_pct": pct(flags["fundamental_available"].sum(), len(flags)),
            "tactical_top15_missing_quality": int(numeric(tactical.head(15), "quality_score").isna().sum()),
            "core_top15_missing_quality": int(numeric(core.head(15), "quality_score").isna().sum()),
        },
        "score_columns": rows,
        "raw_inputs": raw_rows,
        "top_reliance": build_top_reliance_rows(tactical, core),
    }


def prepare_day_rows(
    history: pd.DataFrame,
    *,
    fundamentals: pd.DataFrame | None,
    valuation: pd.DataFrame | None,
    investor_flows: pd.DataFrame | None,
    disclosures: pd.DataFrame | None,
    universe: pd.DataFrame | None,
    as_of: str | None,
) -> tuple[pd.DataFrame, str]:
    normalized = normalize_price_history(history)
    requested_as_of = normalize_date(as_of) if as_of else ""
    if requested_as_of:
        normalized = normalized[normalized["source_bas_dt"] <= requested_as_of].copy()
    if normalized.empty:
        return pd.DataFrame(), ""
    signal_day = str(normalized["source_bas_dt"].max())
    featured = add_price_features(normalized)
    featured = attach_universe(featured, universe)
    featured = attach_fundamentals_asof(featured, fundamentals)
    featured = attach_valuation_asof(featured, valuation)
    featured = attach_flow_features(featured, investor_flows)
    day_rows = featured[featured["source_bas_dt"] == signal_day].copy()
    day_rows = attach_disclosure_context(day_rows, disclosures, signal_day, InvestmentRecommenderConfig())
    return day_rows.reset_index(drop=True), signal_day


def add_validity_flags(day_rows: pd.DataFrame, tactical: pd.DataFrame, core: pd.DataFrame) -> pd.DataFrame:
    flags = pd.DataFrame(index=day_rows.index)
    flags["per_valid"] = numeric(day_rows, "per").gt(0)
    flags["pbr_valid"] = numeric(day_rows, "pbr").gt(0)
    flags["dividend_yield_present"] = numeric(day_rows, "dividend_yield").notna()
    flags["roe_present"] = numeric(day_rows, "roe").notna()
    flags["roa_present"] = numeric(day_rows, "roa").notna()
    flags["operating_margin_present"] = numeric(day_rows, "operating_margin").notna()
    flags["net_margin_present"] = numeric(day_rows, "net_margin").notna()
    flags["momentum_120_present"] = numeric(day_rows, "relative_momentum_120d_pct").notna() | numeric(day_rows, "ret_120d_pct").notna()
    flags["momentum_240_present"] = numeric(day_rows, "relative_momentum_240d_pct").notna() | numeric(day_rows, "ret_240d_pct").notna()
    flags["price_vs_ma120_present"] = numeric(day_rows, "price_vs_ma120_pct").notna()
    flags["ret_20d_present"] = numeric(day_rows, "ret_20d_pct").notna()
    flags["ret_60d_present"] = numeric(day_rows, "ret_60d_pct").notna()
    flags["volatility_60d_present"] = numeric(day_rows, "volatility_60d_pct").notna()
    flags["drawdown_60d_present"] = numeric(day_rows, "drawdown_60d_pct").notna()
    flags["market_cap_present"] = numeric(day_rows, "market_cap").notna()
    flags["avg_value_20_present"] = numeric(day_rows, "avg_value_20").notna()
    flags["smart_flow_asof_present"] = text_present(day_rows, "smart_flow_asof_dt")
    flags["smart_flow_nonzero"] = numeric(day_rows, "smart_flow_5d_pressure_pct").ne(0) | numeric(day_rows, "smart_flow_20d_pressure_pct").ne(0)
    flags["event_context_present"] = boolish_series(day_rows, "positive_event_flag") | boolish_series(day_rows, "disclosure_risk_flag")
    flags["fundamental_available"] = boolish_series(day_rows, "fundamental_available")
    for column in ["value_score", "quality_score", "momentum_score", "defensive_score", "liquidity_score"]:
        flags[f"{column}_present"] = numeric(tactical, column).notna()
    flags["raw_final_inputs_present"] = numeric(tactical, "raw_final_score").notna() & numeric(core, "raw_final_score").notna()
    return flags


def audit_score_columns(frame: pd.DataFrame, flags: pd.DataFrame, engine: str, columns: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total = len(frame)
    for column in columns:
        scores = numeric(frame, column)
        input_flags = SCORE_INPUT_FLAGS.get(column, [])
        flag_frame = flags[input_flags] if input_flags else pd.DataFrame(index=flags.index)
        if input_flags:
            raw_full = flag_frame.all(axis=1)
            raw_any = flag_frame.any(axis=1)
        else:
            raw_full = pd.Series(True, index=flags.index)
            raw_any = pd.Series(True, index=flags.index)
        fallback = fallback_mask(column, flags)
        rows.append(
            {
                "engine": engine,
                "score_column": column,
                "rows": int(total),
                "score_non_null": int(scores.notna().sum()),
                "score_coverage_pct": pct(scores.notna().sum(), total),
                "raw_full_count": int(raw_full.sum()),
                "raw_full_pct": pct(raw_full.sum(), total),
                "raw_partial_count": int((raw_any & ~raw_full).sum()),
                "raw_partial_pct": pct((raw_any & ~raw_full).sum(), total),
                "raw_none_count": int((~raw_any).sum()),
                "raw_none_pct": pct((~raw_any).sum(), total),
                "fallback_neutral_count": int(fallback.sum()),
                "fallback_neutral_pct": pct(fallback.sum(), total),
                "midband_45_55_count": int(scores.between(45, 55, inclusive="both").sum()),
                "midband_45_55_pct": pct(scores.between(45, 55, inclusive="both").sum(), total),
                "min": round_float(scores.min()),
                "median": round_float(scores.median()),
                "max": round_float(scores.max()),
            }
        )
    return rows


def fallback_mask(column: str, flags: pd.DataFrame) -> pd.Series:
    if column == "flow_score":
        return ~flags["smart_flow_asof_present"]
    if column == "event_score":
        return ~flags["event_context_present"]
    if column == "event_flow_confirmation_score":
        return ~flags["event_context_present"] & ~flags["smart_flow_asof_present"]
    return pd.Series(False, index=flags.index)


def audit_raw_inputs(day_rows: pd.DataFrame, flags: pd.DataFrame) -> list[dict[str, object]]:
    raw_specs = {
        "valuation": ["per_valid", "pbr_valid", "dividend_yield_present"],
        "quality": ["roe_present", "roa_present", "operating_margin_present", "net_margin_present"],
        "momentum": ["momentum_120_present", "momentum_240_present", "price_vs_ma120_present", "ret_60d_present"],
        "defensive": ["volatility_60d_present", "drawdown_60d_present", "price_vs_ma120_present"],
        "flow": ["smart_flow_asof_present", "smart_flow_nonzero"],
        "liquidity": ["market_cap_present", "avg_value_20_present"],
        "event": ["event_context_present"],
    }
    total = len(day_rows)
    rows = []
    for group, columns in raw_specs.items():
        for column in columns:
            series = flags[column]
            rows.append({"group": group, "input_flag": column, "present_count": int(series.sum()), "present_pct": pct(series.sum(), total)})
    return rows


def build_top_reliance_rows(tactical: pd.DataFrame, core: pd.DataFrame) -> dict[str, list[dict[str, object]]]:
    return {
        "tactical_top15": top_reliance(tactical),
        "core_top15": top_reliance(core),
    }


def top_reliance(frame: pd.DataFrame) -> list[dict[str, object]]:
    rows = []
    for row in frame.head(15).to_dict("records"):
        rows.append(
            {
                "rank": int(row.get("rank", 0) or 0),
                "ticker": str(row.get("ticker", "")),
                "company": str(row.get("company", "")),
                "technique": str(row.get("technique", "")),
                "final_score": round_float(row.get("final_score")),
                "quality_score": round_float(row.get("quality_score")),
                "fundamental_available": bool(row.get("fundamental_available", False)),
                "flow_score": round_float(row.get("flow_score")),
                "smart_flow_asof_dt": str(row.get("smart_flow_asof_dt", "")),
                "positive_event_flag": bool(row.get("positive_event_flag", False)),
                "disclosure_risk_flag": bool(row.get("disclosure_risk_flag", False)),
            }
        )
    return rows


def write_audit_outputs(audit: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"score_coverage_audit_{stamp}.json"
    md_path = output_dir / f"score_coverage_audit_{stamp}.md"
    latest_json = output_dir / "latest_summary.json"
    latest_md = output_dir / "latest.md"
    safe = json_ready(audit)
    text = json.dumps(safe, ensure_ascii=False, indent=2, allow_nan=False)
    md = render_markdown(audit)
    json_path.write_text(text, encoding="utf-8")
    latest_json.write_text(text, encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    latest_md.write_text(md, encoding="utf-8")
    return {"json": json_path, "md": md_path, "latest_json": latest_json, "latest_md": latest_md}


def render_markdown(audit: dict[str, object]) -> str:
    summary = audit["summary"]
    lines = [
        "# KR DayPilot Score Coverage Audit",
        "",
        f"- Generated at: {audit['generated_at']}",
        f"- Signal day: {summary['signal_day']}",
        f"- Universe rows: {summary['universe_rows']}",
        f"- Fundamental coverage: {summary['fundamental_available_pct']}%",
        f"- Flow as-of coverage: {summary['flow_asof_coverage_pct']}%",
        f"- Active event coverage: {summary['event_active_pct']}%",
        f"- Tactical top15 missing quality: {summary['tactical_top15_missing_quality']}",
        f"- Core top15 missing quality: {summary['core_top15_missing_quality']}",
        f"- High risk score columns: {', '.join(summary['high_risk_columns']) or 'none'}",
        "",
        "## Score Columns",
        "",
        "|engine|score_column|coverage|raw_full|raw_partial|raw_none|neutral/fallback|midband_45_55|median|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in audit["score_columns"]:
        lines.append(
            f"|{row['engine']}|{row['score_column']}|{row['score_coverage_pct']}%|{row['raw_full_pct']}%|"
            f"{row['raw_partial_pct']}%|{row['raw_none_pct']}%|{row['fallback_neutral_pct']}%|"
            f"{row['midband_45_55_pct']}%|{row['median']}|"
        )
    lines.extend(
        [
            "",
            "## Raw Inputs",
            "",
            "|group|input_flag|present|",
            "|---|---:|---:|",
        ]
    )
    for row in audit["raw_inputs"]:
        lines.append(f"|{row['group']}|{row['input_flag']}|{row['present_pct']}%|")
    return "\n".join(lines) + "\n"


def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce").astype("Float64")


def boolish_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].map(lambda value: str(value).strip().lower() in {"1", "true", "t", "yes", "y"})


def text_present(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].fillna("").astype(str).str.strip().ne("")


def pct(count: object, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(count) / float(total) * 100.0, 2)


def round_float(value: object) -> float | None:
    try:
        if pd.isna(value):
            return None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
