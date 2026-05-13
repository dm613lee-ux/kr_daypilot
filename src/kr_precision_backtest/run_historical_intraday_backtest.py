from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .intraday_strategy import evaluate_intraday_day
from .policy import load_policy


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_ROOT = PROGRAM_ROOT / "data" / "historical_intraday" / "minute_bars"
DEFAULT_METADATA = PROGRAM_ROOT / "data" / "historical_intraday" / "candidates.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "historical_intraday"
GOOD_BACKFILL_STATUSES = {"collected", "skipped_existing"}
HTML_TEXT = {
    "empty": "\uacfc\uac70 \ubd84\ubd09 \uac80\uc99d \uacb0\uacfc\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.",
    "title": "KR DayPilot \uacfc\uac70 \ubd84\ubd09 \uc2dc\ubbac\ub808\uc774\uc158",
    "generated_at": "\uc0dd\uc131 \uc2dc\uac01:",
    "candidate_files": "\ud6c4\ubcf4 \ud30c\uc77c",
    "entry_signals": "\uc2e4\uc81c \uc9c4\uc785 \uc2e0\ud638",
    "signal_success_rate": "\uc9c4\uc785 \uae30\uc900 \uc131\uacf5\ub960",
    "candidate_success_rate": "\ud6c4\ubcf4 \uae30\uc900 \uc131\uacf5\ub960",
    "successes": "\uc131\uacf5",
    "failure_exits": "\uc2e4\ud328\ucca0\uc218",
    "no_signals": "\ubb34\uc9c4\uc785",
    "avg_net_return": "\ud3c9\uade0 \uc21c\uc218\uc775\ub960",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run historical intraday simulation for backfilled KR DayPilot bars.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    rows = []
    for path, meta in list_backfilled_files(args.root, args.metadata):
        if not candidate_passes_policy(meta, policy):
            continue
        bars = pd.read_csv(path, dtype={"ticker": str, "date": str, "time": str})
        result = asdict(evaluate_intraday_day(bars, policy))
        rows.append({**meta, **result, "source_path": str(path)})

    results = pd.DataFrame(rows)
    summary = summarize(results)
    paths = write_outputs(results, summary, args.output)
    print("Historical intraday simulation complete.")
    print(f"files={summary['files']}, candidates={summary['candidates']}, signals={summary['signals']}")
    print(f"success_rate={summary['success_rate']}%, avg_net_return_pct={summary['avg_net_return_pct']}%")
    print(f"HTML={paths['html']}")
    return 0


def list_backfilled_files(root: Path, metadata_path: Path) -> list[tuple[Path, dict[str, object]]]:
    metadata_rows = read_metadata_rows(metadata_path)
    if metadata_rows:
        items = []
        for meta in metadata_rows:
            source_path = Path(str(meta.pop("_path", "")))
            if source_path.exists():
                items.append((source_path, meta))
        return sorted(
            items,
            key=lambda item: (
                str(item[1].get("entry_date", "")),
                str(item[1].get("rank", "")),
                str(item[1].get("ticker", "")),
            ),
        )

    if not root.exists():
        return []
    return [(path, {}) for path in sorted(root.rglob("*.csv"))]


def read_metadata_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path, dtype={"ticker": str, "entry_date": str, "reference_day": str})
    result = []
    for _, row in frame.iterrows():
        ticker = str(row.get("ticker", "")).zfill(6)
        entry_date = _compact_date(row.get("entry_date", ""))
        status = str(row.get("status", ""))
        source_path = str(row.get("path", "")).strip()
        if status not in GOOD_BACKFILL_STATUSES or not source_path:
            continue
        result.append(
            {
                "reference_day": _display_date(row.get("reference_day", "")),
                "entry_date": _display_date(entry_date),
                "rank": row.get("rank", ""),
                "ticker": ticker,
                "company": row.get("company", ""),
                "market": row.get("market", ""),
                "signal_score": row.get("signal_score", ""),
                "day_change_pct": row.get("day_change_pct", ""),
                "market_median_change_pct": row.get("market_median_change_pct", ""),
                "value_ratio_20": row.get("value_ratio_20", ""),
                "close_location_pct": row.get("close_location_pct", ""),
                "lower_tail_recovery_pct": row.get("lower_tail_recovery_pct", ""),
                "close_vs_open_pct": row.get("close_vs_open_pct", ""),
                "distance_from_60d_high_pct": row.get("distance_from_60d_high_pct", ""),
                "entry_gap_pct": row.get("entry_gap_pct", ""),
                "backfill_status": row.get("status", ""),
                "backfill_rows": row.get("rows", ""),
                "_path": source_path,
            }
        )
    return result


def candidate_passes_policy(meta: dict[str, object], policy: object) -> bool:
    close_location = _meta_float(meta, "close_location_pct")
    value_ratio = _meta_float(meta, "value_ratio_20")
    distance = _meta_float(meta, "distance_from_60d_high_pct")
    day_change = _meta_float(meta, "day_change_pct")
    if day_change is not None and day_change < policy.min_reference_day_change_pct:
        return False
    if close_location is not None and close_location < policy.min_reliable_close_location_pct:
        return False
    if value_ratio is not None and value_ratio > policy.max_reliable_value_ratio_20:
        return False
    if distance is not None and distance > policy.max_reliable_distance_from_60d_high_pct:
        return False
    return True


def summarize(results: pd.DataFrame) -> dict[str, object]:
    if results.empty:
        return {
            "files": 0,
            "candidates": 0,
            "signals": 0,
            "successes": 0,
            "failure_exits": 0,
            "time_exits": 0,
            "no_signals": 0,
            "success_rate": 0.0,
            "candidate_success_rate": 0.0,
            "avg_net_return_pct": 0.0,
        }
    signals = results[results["exit_reason"] != "no_signal"].copy()
    successes = int((signals["exit_reason"] == "target_hit").sum())
    failure_exits = int((signals["exit_reason"].isin(["stop_loss", "ambiguous_stop_first"])).sum())
    time_exits = int((signals["exit_reason"] == "time_exit").sum())
    no_signals = int((results["exit_reason"] == "no_signal").sum())
    avg_net = float(signals["net_return_pct"].mean()) if not signals.empty else 0.0
    return {
        "files": int(len(results)),
        "candidates": int(len(results)),
        "signals": int(len(signals)),
        "successes": successes,
        "failure_exits": failure_exits,
        "time_exits": time_exits,
        "no_signals": no_signals,
        "success_rate": _pct(successes, len(signals)),
        "candidate_success_rate": _pct(successes, len(results)),
        "avg_net_return_pct": round(avg_net, 3),
    }


def write_outputs(results: pd.DataFrame, summary: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"historical_intraday_{stamp}.csv"
    json_path = output_dir / f"historical_intraday_summary_{stamp}.json"
    html_path = output_dir / f"historical_intraday_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"
    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {"generated_at": datetime.now(tz=KST).isoformat(), "summary": summary}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(results, summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"csv": csv_path, "json": json_path, "html": html_path}


def render_html(results: pd.DataFrame, summary: dict[str, object]) -> str:
    if results.empty:
        table = f"<p>{escape(HTML_TEXT['empty'])}</p>"
    else:
        sort_columns = [column for column in ["date", "rank", "ticker"] if column in results.columns]
        table_frame = results.sort_values(sort_columns).tail(100) if sort_columns else results.tail(100)
        table = table_frame.to_html(index=False, escape=True)
    title = HTML_TEXT["title"]
    generated_at = HTML_TEXT["generated_at"]
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 6px 7px; text-align: right; }}
    th:nth-child(1), td:nth-child(1), th:nth-child(4), td:nth-child(4), th:nth-child(5), td:nth-child(5) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <p>{escape(generated_at)} {escape(datetime.now(tz=KST).isoformat(timespec="seconds"))}</p>
  <div class="grid">
    {card(HTML_TEXT["candidate_files"], summary.get("files", 0))}
    {card(HTML_TEXT["entry_signals"], summary.get("signals", 0))}
    {card(HTML_TEXT["signal_success_rate"], str(summary.get("success_rate", 0)) + "%")}
    {card(HTML_TEXT["candidate_success_rate"], str(summary.get("candidate_success_rate", 0)) + "%")}
    {card(HTML_TEXT["successes"], summary.get("successes", 0))}
    {card(HTML_TEXT["failure_exits"], summary.get("failure_exits", 0))}
    {card(HTML_TEXT["no_signals"], summary.get("no_signals", 0))}
    {card(HTML_TEXT["avg_net_return"], str(summary.get("avg_net_return_pct", 0)) + "%")}
  </div>
  {table}
</body>
</html>"""


def card(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def _compact_date(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text[:8] if len(text) >= 8 else ""


def _display_date(value: object) -> str:
    text = _compact_date(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else str(value)


def _meta_float(meta: dict[str, object], key: str) -> float | None:
    value = meta.get(key)
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(numerator: int | float, denominator: int | float) -> float:
    denominator = float(denominator)
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / denominator * 100.0, 2)


if __name__ == "__main__":
    raise SystemExit(main())
