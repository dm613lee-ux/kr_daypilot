from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = PROGRAM_ROOT / "output" / "swing_backtest" / "latest.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "feature_validation"

NUMERIC_FEATURES = [
    "alpha_score",
    "ret_1d_pct",
    "ret_5d_pct",
    "value_ratio_20",
    "trading_value_z_20",
    "close_location_pct",
    "relative_strength_5d_pct",
    "market_advancing_ratio",
    "market_ret_1d_median_pct",
    "sector_relative_strength_5d_pct",
    "foreign_net_buy_value_z20",
    "institution_net_buy_value_z20",
    "retail_net_buy_value_z20",
    "short_sale_value_ratio",
    "credit_balance_ratio",
    "disclosure_count",
    "entry_gap_pct",
]

CATEGORICAL_FEATURES = [
    "market",
    "market_regime",
    "sector_group",
    "sector_source",
    "investor_flow_available",
    "short_credit_available",
    "disclosure_risk_flag",
    "paper_order_rank",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze which swing features reduce stop-loss rate.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-order-rank", type=int, default=2)
    parser.add_argument("--min-bin-count", type=int, default=20)
    args = parser.parse_args()

    results = pd.read_csv(args.results, dtype={"ticker": str})
    analysis, summary = analyze_features(
        results,
        max_order_rank=max(args.max_order_rank, 1),
        min_bin_count=max(args.min_bin_count, 1),
        source_path=args.results,
    )
    paths = write_outputs(analysis, summary, args.output)

    print("KR DayPilot feature stop analysis complete.")
    print(f"Filled trades analyzed: {summary['filled_trades']}")
    print(f"Baseline stop rate: {summary['baseline_stop_rate']}%")
    print(f"Baseline target rate: {summary['baseline_target_rate']}%")
    print(f"Research status: {summary['research_status']}")
    print(f"HTML: {paths['html']}")
    return 0


def analyze_features(
    results: pd.DataFrame,
    *,
    max_order_rank: int,
    min_bin_count: int,
    source_path: Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = normalize_results(results)
    orders = frame[frame["paper_order_rank"] <= max_order_rank].copy()
    filled = orders[orders["paper_filled"]].copy()
    baseline = metrics(filled)
    rows: list[dict[str, object]] = []

    for feature in [col for col in CATEGORICAL_FEATURES if col in filled.columns]:
        series = filled[feature].fillna("missing").astype(str)
        for group_value, group in filled.groupby(series, dropna=False):
            rows.append(group_row(feature, "categorical", str(group_value), group, baseline, min_bin_count))

    for feature in [col for col in NUMERIC_FEATURES if col in filled.columns]:
        numeric = pd.to_numeric(filled[feature], errors="coerce")
        valid = filled[numeric.notna()].copy()
        if valid.empty or numeric.nunique(dropna=True) < 2:
            continue
        valid["_feature_value"] = numeric[numeric.notna()]
        try:
            bins = pd.qcut(valid["_feature_value"], q=3, duplicates="drop")
        except ValueError:
            continue
        valid["_feature_bin"] = bins.astype(str)
        for group_value, group in valid.groupby("_feature_bin", dropna=False):
            rows.append(group_row(feature, "numeric_tertile", str(group_value), group, baseline, min_bin_count))

    analysis = pd.DataFrame(rows)
    if not analysis.empty:
        analysis = analysis.sort_values(
            ["sample_status", "stop_rate_delta_pp", "target_rate_delta_pp", "count"],
            ascending=[True, True, False, False],
        ).reset_index(drop=True)
    summary = {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "source_path": str(source_path),
        "population_filter": f"paper_order_rank <= {max_order_rank} and paper_filled == true",
        "max_order_rank": max_order_rank,
        "recommendations": int(len(frame)),
        "eligible_orders": int(len(orders)),
        "filled_trades": int(len(filled)),
        "target_hits": baseline["target_hits"],
        "stop_hits": baseline["stop_hits"],
        "time_exits": baseline["time_exits"],
        "baseline_target_rate": baseline["target_rate"],
        "baseline_stop_rate": baseline["stop_rate"],
        "baseline_time_exit_rate": baseline["time_exit_rate"],
        "baseline_avg_net_return_after_cost_pct": baseline["avg_net_return_after_cost_pct"],
        "baseline_median_net_return_after_cost_pct": baseline["median_net_return_after_cost_pct"],
        "target_wilson_low": baseline["target_wilson_low"],
        "stop_wilson_high": baseline["stop_wilson_high"],
        "min_bin_count": min_bin_count,
        "analysis_rows": int(len(analysis)),
        "research_status": research_status(len(filled)),
        "note": "탐색 리포트입니다. 표본 300건 이상과 시간순 holdout 검증 전에는 자동 차단 규칙으로 승격하지 않습니다.",
    }
    return analysis, summary


def normalize_results(results: pd.DataFrame) -> pd.DataFrame:
    frame = results.copy()
    if "paper_order_rank" not in frame.columns:
        frame["paper_order_rank"] = 999
    frame["paper_order_rank"] = pd.to_numeric(frame["paper_order_rank"], errors="coerce").fillna(999).astype(int)
    for column in ["paper_filled", "target_hit_d1_d3", "stop_hit_d1_d3", "time_exit"]:
        if column not in frame.columns:
            frame[column] = False
        frame[column] = frame[column].map(boolish)
    for column in ["net_return_after_cost_pct", "max_adverse_excursion_pct"]:
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return frame


def metrics(frame: pd.DataFrame) -> dict[str, object]:
    total = len(frame)
    target_hits = int(frame["target_hit_d1_d3"].sum()) if total else 0
    stop_hits = int(frame["stop_hit_d1_d3"].sum()) if total else 0
    time_exits = int(frame["time_exit"].sum()) if total else 0
    target_low, _ = wilson(target_hits, total)
    _, stop_high = wilson(stop_hits, total)
    return {
        "count": int(total),
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "time_exits": time_exits,
        "target_rate": pct(target_hits, total),
        "stop_rate": pct(stop_hits, total),
        "time_exit_rate": pct(time_exits, total),
        "avg_net_return_after_cost_pct": round(float(frame["net_return_after_cost_pct"].mean()), 3) if total else 0.0,
        "median_net_return_after_cost_pct": round(float(frame["net_return_after_cost_pct"].median()), 3) if total else 0.0,
        "target_wilson_low": round(target_low, 2),
        "stop_wilson_high": round(stop_high, 2),
    }


def group_row(
    feature: str,
    feature_type: str,
    group_value: str,
    group: pd.DataFrame,
    baseline: dict[str, object],
    min_bin_count: int,
) -> dict[str, object]:
    stats = metrics(group)
    stop_delta = round(float(stats["stop_rate"]) - float(baseline["stop_rate"]), 2)
    target_delta = round(float(stats["target_rate"]) - float(baseline["target_rate"]), 2)
    coverage = pct(int(stats["count"]), int(baseline["count"]))
    sample_status = "ok" if int(stats["count"]) >= min_bin_count else "low_sample"
    recommendation = "watch"
    if sample_status == "low_sample":
        recommendation = "low_sample"
    elif stop_delta <= -10 and target_delta >= -5:
        recommendation = "candidate_reduce_stop"
    elif stop_delta >= 10:
        recommendation = "candidate_avoid"
    return {
        "feature": feature,
        "feature_type": feature_type,
        "group_value": group_value,
        "count": stats["count"],
        "coverage_pct": coverage,
        "target_hits": stats["target_hits"],
        "stop_hits": stats["stop_hits"],
        "time_exits": stats["time_exits"],
        "target_rate": stats["target_rate"],
        "stop_rate": stats["stop_rate"],
        "time_exit_rate": stats["time_exit_rate"],
        "stop_rate_delta_pp": stop_delta,
        "target_rate_delta_pp": target_delta,
        "avg_net_return_after_cost_pct": stats["avg_net_return_after_cost_pct"],
        "median_net_return_after_cost_pct": stats["median_net_return_after_cost_pct"],
        "target_wilson_low": stats["target_wilson_low"],
        "stop_wilson_high": stats["stop_wilson_high"],
        "sample_status": sample_status,
        "recommendation": recommendation,
    }


def write_outputs(analysis: pd.DataFrame, summary: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"feature_stop_analysis_{stamp}.csv"
    json_path = output_dir / f"feature_stop_summary_{stamp}.json"
    html_path = output_dir / f"feature_stop_analysis_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"
    analysis.to_csv(csv_path, index=False, encoding="utf-8-sig")
    analysis.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    json_payload = {"summary": summary}
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(analysis, summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"csv": csv_path, "json": json_path, "html": html_path, "latest_html": latest_html}


def render_html(analysis: pd.DataFrame, summary: dict[str, object]) -> str:
    if analysis.empty:
        table_html = "<p class=\"note\">분석 가능한 feature 행이 없습니다.</p>"
    else:
        display_columns = [
            "feature",
            "feature_type",
            "group_value",
            "count",
            "coverage_pct",
            "stop_rate",
            "stop_rate_delta_pp",
            "target_rate",
            "target_rate_delta_pp",
            "avg_net_return_after_cost_pct",
            "target_wilson_low",
            "stop_wilson_high",
            "sample_status",
            "recommendation",
        ]
        table_html = analysis[display_columns].head(120).to_html(index=False, escape=True, classes="data")
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Feature Stop Analysis</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .note {{ color: #667085; line-height: 1.6; }}
    .warning {{ background: #fff4e5; border: 1px solid #fdb022; border-radius: 8px; padding: 14px 16px; margin: 18px 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 22px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 25px; font-weight: 700; margin-top: 8px; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: right; }}
    table.data th:nth-child(1), table.data td:nth-child(1),
    table.data th:nth-child(2), table.data td:nth-child(2),
    table.data th:nth-child(3), table.data td:nth-child(3),
    table.data th:nth-child(13), table.data td:nth-child(13),
    table.data th:nth-child(14), table.data td:nth-child(14) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>Feature별 손절률 감소 검증</h1>
  <p class="note">새 1~3일 스윙 백테스트 결과에서 어떤 feature 구간이 실제 손절률을 낮추는지 탐색합니다.</p>
  <div class="warning">
    현재 결과는 실전 자동 주문 허가 기준이 아닙니다. 표본 300건 이상과 시간순 holdout 검증 전에는 feature를 자동 차단 규칙으로 승격하지 않습니다.
  </div>
  <div class="grid">
    {metric("판정", summary.get("research_status", ""))}
    {metric("분석 체결", summary.get("filled_trades", 0))}
    {metric("기준 손절률", f"{summary.get('baseline_stop_rate', 0)}%")}
    {metric("기준 목표도달률", f"{summary.get('baseline_target_rate', 0)}%")}
    {metric("시간청산률", f"{summary.get('baseline_time_exit_rate', 0)}%")}
    {metric("평균 순수익률", f"{summary.get('baseline_avg_net_return_after_cost_pct', 0)}%")}
    {metric("목표 Wilson 하한", f"{summary.get('target_wilson_low', 0)}%")}
    {metric("손절 Wilson 상한", f"{summary.get('stop_wilson_high', 0)}%")}
  </div>
  <h2>해석 기준</h2>
  <p class="note">
    모집단: {escape(str(summary.get("population_filter", "")))}.
    recommendation이 candidate_reduce_stop이어도 표본이 충분하지 않으면 관찰 후보입니다.
    stop_rate_delta_pp는 기준 손절률 대비 증감입니다. 음수일수록 손절률이 낮은 구간입니다.
  </p>
  <h2>Feature 구간별 결과</h2>
  {table_html}
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def research_status(filled_count: int) -> str:
    if filled_count < 100:
        return "탐색: 표본 부족"
    if filled_count < 300:
        return "관찰: 채택 전"
    return "검증 후보"


def boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y"}


def pct(count: int, total: int) -> float:
    return round(count / total * 100.0, 2) if total else 0.0


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.96
    phat = successes / total
    denom = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    half = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return (center - half) * 100.0, (center + half) * 100.0


if __name__ == "__main__":
    raise SystemExit(main())
