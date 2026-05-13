from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .intraday_strategy import evaluate_intraday_day
from .policy import load_policy


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROGRAM_ROOT / "data" / "intraday" / "minute_bars"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "intraday"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KR DayPilot intraday 1-minute strategy validation.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    minute_files = _minute_files(args.input)
    rows = []
    for path in minute_files:
        bars = pd.read_csv(path, dtype={"ticker": str, "date": str, "time": str})
        rows.append(asdict(evaluate_intraday_day(bars, policy)))

    results = pd.DataFrame(rows)
    summary = _summary(results)
    paths = _write_outputs(results, summary, args.output)

    print("Phase 1B intraday validation complete.")
    print(f"Minute files: {len(minute_files)}")
    print(f"Signals: {summary['signals']}")
    print(f"Successes: {summary['successes']}")
    print(f"Failures: {summary['failures']}")
    print(f"Success rate: {summary['success_rate']}%")
    print(f"Average net return: {summary['avg_net_return_pct']}%")
    print(f"HTML: {paths['html']}")
    print(f"CSV: {paths['csv']}")
    print(f"JSON: {paths['json']}")
    return 0


def _minute_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.csv") if "latest" not in path.parts)


def _summary(results: pd.DataFrame) -> dict[str, object]:
    if results.empty:
        return {
            "files": 0,
            "signals": 0,
            "successes": 0,
            "failures": 0,
            "time_exits": 0,
            "success_rate": 0.0,
            "avg_net_return_pct": 0.0,
            "limitation": _limitation(),
        }
    signaled = results[results["exit_reason"] != "no_signal"].copy()
    successes = int((signaled["exit_reason"] == "target_hit").sum())
    failures = int((signaled["exit_reason"].isin(["stop_loss", "ambiguous_stop_first"])).sum())
    time_exits = int((signaled["exit_reason"] == "time_exit").sum())
    avg_net = float(signaled["net_return_pct"].mean()) if not signaled.empty else 0.0
    return {
        "files": int(len(results)),
        "signals": int(len(signaled)),
        "successes": successes,
        "failures": failures,
        "time_exits": time_exits,
        "success_rate": round(successes / len(signaled) * 100.0, 2) if len(signaled) else 0.0,
        "avg_net_return_pct": round(avg_net, 3),
        "limitation": _limitation(),
    }


def _write_outputs(results: pd.DataFrame, summary: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"phase1b_intraday_{stamp}.csv"
    json_path = output_dir / f"phase1b_intraday_summary_{stamp}.json"
    html_path = output_dir / f"phase1b_intraday_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"

    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {"summary": summary}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = _render_html(results, summary)
    html_path.write_text(html, encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "html": html_path}


def _render_html(results: pd.DataFrame, summary: dict[str, object]) -> str:
    table = results.to_html(index=False, escape=True) if not results.empty else "<p>분봉 데이터가 없습니다.</p>"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Phase 1B</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: right; }}
  </style>
</head>
<body>
  <h1>KR DayPilot Phase 1B 분봉 검증</h1>
  <p>{summary.get("limitation", "")}</p>
  <div class="grid">
    {card("파일 수", summary.get("files", 0))}
    {card("신호 수", summary.get("signals", 0))}
    {card("성공률", str(summary.get("success_rate", 0)) + "%")}
    {card("평균 순수익률", str(summary.get("avg_net_return_pct", 0)) + "%")}
  </div>
  {table}
</body>
</html>"""


def card(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div></div>'


def _limitation() -> str:
    return "KIS 당일 분봉 기반 검증입니다. 과거 여러 날 검증은 수집 데이터가 누적된 뒤 가능합니다."


if __name__ == "__main__":
    raise SystemExit(main())

