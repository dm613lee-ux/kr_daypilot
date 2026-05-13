from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import add_daily_proxy_features, load_price_history
from .eod_context import add_eod_context_features
from .policy import load_policy, policy_to_dict
from .swing_strategy import (
    add_swing_features,
    output_columns,
    select_swing_candidates,
    simulate_swing_trade,
    summarize_swing_results,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "swing_backtest"
DEFAULT_EOD_CONTEXT = PROGRAM_ROOT / "data" / "eod_context"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KR DayPilot 1-3 day swing paper-order backtest.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--eod-context-dir", type=Path, default=DEFAULT_EOD_CONTEXT)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--max-reference-days", type=int, default=250)
    parser.add_argument("--max-candidates", type=int, default=0)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    max_candidates = args.max_candidates if args.max_candidates > 0 else policy.max_display_candidates
    history = add_swing_features(add_daily_proxy_features(load_price_history(args.price_history)))
    history = add_eod_context_features(history, context_dir=args.eod_context_dir, universe_path=args.universe)
    results, summary = run_swing_backtest(history, policy, max_reference_days=max(args.max_reference_days, 1), max_candidates=max_candidates)
    paths = write_outputs(results, summary, policy, args.output)

    print("KR DayPilot swing backtest complete.")
    print(f"Recommendations: {summary.get('recommendations', 0)}")
    print(f"Paper orders: {summary.get('paper_orders', 0)}")
    print(f"Filled: {summary.get('paper_filled', 0)}")
    print(f"Target success rate: {summary.get('target_success_rate', 0)}%")
    print(f"Average net return: {summary.get('avg_net_return_after_cost_pct', 0)}%")
    print(f"Research pass: {summary.get('research_pass', False)}")
    print(f"HTML: {paths['html']}")
    return 0


def run_swing_backtest(
    history: pd.DataFrame,
    policy: object,
    *,
    max_reference_days: int,
    max_candidates: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    days = sorted(history["source_bas_dt"].astype(str).unique())
    eligible_days = days[80:-3]
    reference_days = eligible_days[-max_reference_days:]
    by_day = {day: frame.copy() for day, frame in history.groupby("source_bas_dt")}
    by_ticker = {ticker: frame.sort_values("source_bas_dt").reset_index(drop=True) for ticker, frame in history.groupby("ticker")}
    rows: list[dict[str, object]] = []

    for reference_day in reference_days:
        candidates = select_swing_candidates(by_day.get(reference_day, pd.DataFrame()), policy, max_candidates=max_candidates)
        for rank, candidate in enumerate(candidates.to_dict("records"), start=1):
            ticker = str(candidate.get("ticker", "")).zfill(6)
            label = simulate_swing_trade(candidate, by_ticker.get(ticker, pd.DataFrame()), reference_day, policy, order_rank=rank)
            rows.append(label)

    results = pd.DataFrame(rows)
    if not results.empty:
        preferred = [column for column in output_columns() if column in results.columns]
        rest = [column for column in results.columns if column not in preferred]
        results = results[preferred + rest].copy()
    summary = summarize_swing_results(results, policy)
    if reference_days:
        summary["reference_start"] = _display_day(reference_days[0])
        summary["reference_end"] = _display_day(reference_days[-1])
        summary["reference_days"] = len(reference_days)
    return results, summary


def write_outputs(results: pd.DataFrame, summary: dict[str, object], policy: object, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"swing_backtest_{stamp}.csv"
    json_path = output_dir / f"swing_backtest_summary_{stamp}.json"
    html_path = output_dir / f"swing_backtest_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"
    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {"summary": summary, "policy": policy_to_dict(policy)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(results, summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"csv": csv_path, "json": json_path, "html": html_path, "latest_html": latest_html}


def render_html(results: pd.DataFrame, summary: dict[str, object]) -> str:
    verdict = "통과" if summary.get("research_pass") else "검증 부족"
    table_frame = results.tail(120) if not results.empty else pd.DataFrame()
    table_html = table_frame.to_html(index=False, escape=True, classes="data") if not table_frame.empty else "<p>검증 결과가 없습니다.</p>"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot 스윙 페이퍼 검증</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .note {{ color: #667085; line-height: 1.6; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 22px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 26px; font-weight: 700; margin-top: 8px; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 6px 7px; text-align: right; }}
    table.data th:nth-child(4), table.data td:nth-child(4),
    table.data th:nth-child(5), table.data td:nth-child(5),
    table.data th:nth-child(22), table.data td:nth-child(22) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot 1~3일 스윙 페이퍼 검증</h1>
  <p class="note">실전 주문이 아니라, 지정가 매수-목표가-손절가-3거래일 시간청산 규칙을 과거 일봉으로 검증한 결과입니다.</p>
  <div class="grid">
    {metric("판정", verdict)}
    {metric("페이퍼 주문", summary.get("paper_orders", 0))}
    {metric("체결", summary.get("paper_filled", 0))}
    {metric("체결률", f'{summary.get("fill_rate", 0)}%')}
    {metric("목표 도달률", f'{summary.get("target_success_rate", 0)}%')}
    {metric("손절률", f'{summary.get("stop_rate", 0)}%')}
    {metric("Wilson 하한", f'{summary.get("wilson_low", 0)}%')}
    {metric("평균 순수익률", f'{summary.get("avg_net_return_after_cost_pct", 0)}%')}
  </div>
  <h2>검증 조건</h2>
  <p class="note">
    기준일 {escape(str(summary.get("reference_start", "")))} ~ {escape(str(summary.get("reference_end", "")))} /
    D+1~D+3 보유 / 미체결 폐기 / 비용 차감 후 수익률 기준
  </p>
  <h2>최근 결과</h2>
  {table_html}
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def _display_day(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
