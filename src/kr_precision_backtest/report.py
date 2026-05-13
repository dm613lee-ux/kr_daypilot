from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .policy import Policy, policy_to_dict


KST = ZoneInfo("Asia/Seoul")


def write_outputs(results: pd.DataFrame, summary: dict[str, object], policy: Policy, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"phase1_backtest_{stamp}.csv"
    json_path = output_dir / f"phase1_summary_{stamp}.json"
    html_path = output_dir / f"phase1_report_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"

    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {"summary": summary, "policy": policy_to_dict(policy)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(results, summary, policy)
    html_path.write_text(html, encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "html": html_path, "latest_html": latest_html}


def render_html(results: pd.DataFrame, summary: dict[str, object], policy: Policy) -> str:
    top_rows = results.head(80) if not results.empty else pd.DataFrame()
    table_html = top_rows.to_html(index=False, escape=True, classes="data") if not top_rows.empty else "<p>추천 후보가 없습니다.</p>"
    verdict = "통과" if summary.get("research_pass") else "미통과"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Phase 1 Backtest Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    h1 {{ margin-bottom: 8px; }}
    .note {{ color: #667085; line-height: 1.6; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin: 24px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    .fail {{ color: #b42318; }}
    .pass {{ color: #067647; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: right; }}
    table.data th:nth-child(1), table.data td:nth-child(1),
    table.data th:nth-child(3), table.data td:nth-child(3),
    table.data th:nth-child(4), table.data td:nth-child(4) {{ text-align: left; }}
    code {{ background: #f2f4f7; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>단기 국내주식 고정밀 앱 Phase 1 백테스트</h1>
  <p class="note">{escape(str(summary.get("limitation", "")))}</p>
  <div class="grid">
    {metric("판정", verdict, "pass" if summary.get("research_pass") else "fail")}
    {metric("성공률", f'{summary.get("success_rate", 0)}%')}
    {metric("Wilson 95% 하한", f'{summary.get("wilson_low", 0)}%')}
    {metric("진입 수", str(summary.get("entries", 0)))}
    {metric("성공/실패/시간종료", f'{summary.get("successes", 0)} / {summary.get("failures", 0)} / {summary.get("time_exits", 0)}')}
    {metric("평균 순수익률", f'{summary.get("avg_net_return_pct", 0)}%')}
    {metric("Profit Factor", str(summary.get("profit_factor", 0)))}
    {metric("최대 연속 실패", str(summary.get("max_consecutive_losses", 0)))}
  </div>
  <h2>검증 조건</h2>
  <p>
    기준일: {escape(str(summary.get("reference_start", "")))} ~ {escape(str(summary.get("reference_end", "")))} /
    목표 <code>+{policy.take_profit_pct}%</code>,
    손절 <code>-{policy.stop_loss_pct}%</code>,
    비용 <code>{policy.backtest_round_trip_cost_default_pct}%</code>,
    하루 최대 후보 <code>{policy.max_order_candidates}</code>
  </p>
  <h2>최근 후보 샘플</h2>
  {table_html}
</body>
</html>"""


def metric(label: str, value: str, klass: str = "") -> str:
    return f'<div class="card"><div class="label">{escape(label)}</div><div class="value {klass}">{escape(value)}</div></div>'

