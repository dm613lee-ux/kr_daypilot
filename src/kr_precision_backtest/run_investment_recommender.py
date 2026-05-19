from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .investment_recommender import (
    InvestmentRecommenderConfig,
    build_recommendations,
    json_ready,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_FUNDAMENTALS = PROGRAM_ROOT / "data" / "fundamentals" / "fundamental_snapshots.csv"
DEFAULT_VALUATION = PROGRAM_ROOT / "data" / "fundamentals" / "krx_valuation.csv"
DEFAULT_INVESTOR_FLOWS = PROGRAM_ROOT / "data" / "eod_context" / "investor_flows.csv"
DEFAULT_DISCLOSURES = PROGRAM_ROOT / "data" / "eod_context" / "disclosures.csv"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "investment_recommender"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the KR DayPilot evidence-based investment recommender.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--valuation", type=Path, default=DEFAULT_VALUATION)
    parser.add_argument("--investor-flows", type=Path, default=DEFAULT_INVESTOR_FLOWS)
    parser.add_argument("--disclosures", type=Path, default=DEFAULT_DISCLOSURES)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--as-of", default="", help="Signal date in YYYYMMDD. Defaults to latest available local price date.")
    parser.add_argument("--run-date", default="", help="Execution date in YYYYMMDD. Defaults to today in Asia/Seoul.")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--min-score", type=float, default=62.0)
    parser.add_argument("--min-market-cap-krw", type=float, default=100_000_000_000)
    parser.add_argument("--min-avg-value-20d-krw", type=float, default=5_000_000_000)
    parser.add_argument("--max-price-age-days", type=int, default=7)
    parser.add_argument("--allow-stale-data", action="store_true", help="Allow paper-review output even when local price data is stale.")
    args = parser.parse_args()

    run_date = args.run_date or datetime.now(tz=KST).strftime("%Y%m%d")
    args.run_date = run_date
    config = InvestmentRecommenderConfig(
        min_market_cap_krw=args.min_market_cap_krw,
        min_avg_value_20d_krw=args.min_avg_value_20d_krw,
        min_score_for_review=args.min_score,
        max_price_age_calendar_days=args.max_price_age_days,
        allow_stale_price_data=args.allow_stale_data,
    )
    history = load_required_csv(args.price_history)
    recommendations, summary = build_recommendations(
        history,
        fundamentals=load_optional_csv(args.fundamentals),
        valuation=load_optional_csv(args.valuation),
        investor_flows=load_optional_csv(args.investor_flows),
        disclosures=load_optional_csv(args.disclosures),
        universe=load_optional_csv(args.universe),
        config=config,
        as_of=args.as_of or None,
        run_date=run_date,
        top=max(args.top, 1),
    )
    paths = write_outputs(recommendations, summary, args.output, config, args)
    print("KR DayPilot evidence-based investment recommender complete.")
    print(f"Signal day: {summary.get('signal_day', '')}")
    print(f"State: {summary.get('state', '')}")
    print(f"Recommendations: {summary.get('recommended', 0)}")
    print(f"Blocked: {summary.get('blocked', 0)}")
    freshness = summary.get("data_freshness", {})
    if isinstance(freshness, dict):
        print(f"Price age: {freshness.get('price_age_calendar_days')} calendar days")
        if freshness.get("price_is_stale"):
            print("Warning: local price data is stale; paper-review recommendations are blocked unless --allow-stale-data is used.")
    print(f"HTML: {paths['latest_html']}")
    return 0


def load_required_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    return pd.read_csv(path, dtype={"ticker": str, "isin": str, "source_bas_dt": str}, low_memory=False)


def load_optional_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"ticker": str, "isin": str, "source_bas_dt": str}, low_memory=False)


def write_outputs(
    recommendations: pd.DataFrame,
    summary: dict[str, object],
    output_dir: Path,
    config: InvestmentRecommenderConfig,
    args: argparse.Namespace,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    ordered = order_columns(recommendations)
    csv_path = output_dir / f"investment_recommendations_{stamp}.csv"
    json_path = output_dir / f"investment_recommendations_{stamp}.json"
    html_path = output_dir / f"investment_recommendations_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"

    ordered.to_csv(csv_path, index=False, encoding="utf-8-sig")
    ordered.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "summary": summary,
        "config": config.__dict__,
        "inputs": {
            "price_history": str(args.price_history),
            "fundamentals": str(args.fundamentals),
            "valuation": str(args.valuation),
            "investor_flows": str(args.investor_flows),
            "disclosures": str(args.disclosures),
            "universe": str(args.universe),
            "run_date": str(getattr(args, "run_date", "")),
        },
        "recommendations": ordered.to_dict("records"),
    }
    safe_payload = json_ready(payload)
    json_text = json.dumps(safe_payload, ensure_ascii=False, indent=2, allow_nan=False)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    html = render_html(ordered, summary, config)
    html_path.write_text(html, encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8")
    return {
        "csv": csv_path,
        "json": json_path,
        "html": html_path,
        "latest_csv": latest_csv,
        "latest_json": latest_json,
        "latest_html": latest_html,
    }


def order_columns(frame: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "state",
        "rank",
        "ticker",
        "company",
        "market",
        "sector",
        "technique",
        "final_score",
        "score_components",
        "evidence_summary",
        "paper_plan",
        "block_reason",
        "close",
        "market_cap",
        "avg_value_20",
        "per",
        "pbr",
        "dividend_yield",
        "roe",
        "roa",
        "operating_margin",
        "relative_momentum_120d_pct",
        "relative_momentum_240d_pct",
        "volatility_60d_pct",
        "drawdown_60d_pct",
        "smart_flow_20d_pressure_pct",
        "fundamental_asof_dt",
        "valuation_asof_dt",
        "disclosure_risk_flag",
        "positive_event_flag",
        "disclosure_event_types",
        "disclosure_titles",
    ]
    if frame.empty:
        return pd.DataFrame(columns=preferred)
    result = frame.copy()
    result.insert(1, "rank", range(1, len(result) + 1))
    columns = [col for col in preferred if col in result.columns]
    columns.extend(col for col in result.columns if col not in columns)
    return result[columns]


def render_html(recommendations: pd.DataFrame, summary: dict[str, object], config: InvestmentRecommenderConfig) -> str:
    table = render_table(recommendations)
    freshness_alert = render_freshness_alert(summary)
    freshness = summary.get("data_freshness", {})
    price_age = ""
    if isinstance(freshness, dict):
        price_age = str(freshness.get("price_age_calendar_days", ""))
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot 투자근거 추천</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #1f2937; }}
    h1 {{ margin: 0 0 6px; font-size: 26px; }}
    h2 {{ margin-top: 28px; font-size: 18px; }}
    .note {{ color: #667085; line-height: 1.55; margin: 0 0 18px; }}
    .alert {{ border: 1px solid #f79009; background: #fffaeb; color: #7a2e0e; padding: 12px 14px; border-radius: 8px; margin: 16px 0 18px; line-height: 1.55; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 18px 0 22px; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 12px 14px; background: #fff; }}
    .label {{ color: #667085; font-size: 12px; }}
    .value {{ font-size: 23px; font-weight: 700; margin-top: 5px; }}
    .paper {{ color: #067647; }}
    .watch {{ color: #175cd3; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; vertical-align: top; }}
    th {{ text-align: left; background: #f8fafc; position: sticky; top: 0; }}
    td.num {{ text-align: right; white-space: nowrap; }}
    .state {{ font-weight: 700; white-space: nowrap; }}
    .small {{ color: #667085; font-size: 12px; line-height: 1.55; }}
    code {{ background: #f2f4f7; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>KR DayPilot 투자근거 추천</h1>
  <p class="note">이 화면은 실전 주문 프로그램이 아니라 paper-only 투자 검토용입니다. 후보는 기법별 근거와 차단 사유를 함께 확인해야 합니다.</p>
  {freshness_alert}
  <div class="grid">
    {metric("기준일", str(summary.get("signal_day", "")))}
    {metric("상태", str(summary.get("state", "")), "paper" if summary.get("state") == "paper_review" else "watch")}
    {metric("추천 후보", str(summary.get("recommended", 0)))}
    {metric("검토 후보", str(summary.get("paper_review", 0)))}
    {metric("가격 데이터 나이", price_age)}
    {metric("차단", str(summary.get("blocked", 0)))}
  </div>
  <h2>추천 후보</h2>
  {table}
  <h2>운영 기준</h2>
  <p class="small">
    최소 시가총액 <code>{config.min_market_cap_krw:,.0f}</code>,
    20일 평균거래대금 <code>{config.min_avg_value_20d_krw:,.0f}</code>,
    paper review 최소점수 <code>{config.min_score_for_review}</code>.
    가격 데이터 허용 나이 <code>{config.max_price_age_calendar_days}</code>일.
    리스크 공시는 강제 차단이며, 산출된 진입/목표/손절 가격은 주문 지시가 아니라 검토용 계획입니다.
  </p>
</body>
</html>"""


def render_freshness_alert(summary: dict[str, object]) -> str:
    freshness = summary.get("data_freshness", {})
    if not isinstance(freshness, dict) or not freshness.get("price_is_stale"):
        return ""
    age = freshness.get("price_age_calendar_days")
    max_age = freshness.get("max_price_age_calendar_days")
    run_date = freshness.get("run_date", "")
    signal_day = freshness.get("signal_day", "")
    return (
        '<div class="alert">'
        "로컬 가격 데이터가 오래되었습니다. "
        f"실행일 {escape(str(run_date))}, 기준일 {escape(str(signal_day))}, "
        f"경과 {escape(str(age))}일, 허용 {escape(str(max_age))}일입니다. "
        "기본 설정에서는 paper-review 추천을 차단합니다."
        "</div>"
    )


def render_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p class=\"note\">추천 후보가 없습니다.</p>"
    rows = []
    for _, row in frame.iterrows():
        rows.append(
            "<tr>"
            f"<td class=\"state\">{escape(str(row.get('state', '')))}</td>"
            f"<td class=\"num\">{escape(str(row.get('rank', '')))}</td>"
            f"<td>{escape(str(row.get('ticker', '')))}<br><span class=\"small\">{escape(str(row.get('company', '')))}</span></td>"
            f"<td>{escape(str(row.get('market', '')))}</td>"
            f"<td>{escape(str(row.get('technique', '')))}</td>"
            f"<td class=\"num\">{format_cell(row.get('final_score'))}</td>"
            f"<td>{escape(str(row.get('evidence_summary', '')))}<br><span class=\"small\">{escape(str(row.get('score_components', '')))}</span></td>"
            f"<td>{escape(str(row.get('paper_plan', '')))}</td>"
            f"<td>{escape(str(row.get('block_reason', '')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>상태</th><th>순위</th><th>종목</th><th>시장</th><th>기법</th><th>점수</th><th>근거</th><th>Paper 계획</th><th>차단 사유</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def metric(label: str, value: str, klass: str = "") -> str:
    return f'<div class="card"><div class="label">{escape(label)}</div><div class="value {klass}">{escape(value)}</div></div>'


def format_cell(value: object) -> str:
    try:
        if pd.isna(value):
            return ""
        return escape(f"{float(value):.2f}")
    except (TypeError, ValueError):
        return escape(str(value))


if __name__ == "__main__":
    raise SystemExit(main())
