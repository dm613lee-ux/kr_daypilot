from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RISK_DIR = PROGRAM_ROOT / "output" / "risk_context"
DEFAULT_PERFORMANCE = PROGRAM_ROOT / "data" / "performance" / "trade_log.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "risk_context_validation"

FEATURES = {
    "spread_pct": "low",
    "bid_ask_imbalance_10": "high",
    "trade_strength": "high",
    "index_change_pct": "high",
    "day_change_pct": "high",
    "risk_flag_count": "low",
    "dart_count": "low",
    "news_count": "low",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Join R1 risk context snapshots to actual KR DayPilot outcomes.")
    parser.add_argument("--risk-dir", type=Path, default=DEFAULT_RISK_DIR)
    parser.add_argument("--performance", type=Path, default=DEFAULT_PERFORMANCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-evaluable-entries", type=int, default=10)
    parser.add_argument("--min-pass-entries", type=int, default=3)
    args = parser.parse_args()

    contexts = load_contexts(args.risk_dir)
    performance = load_performance(args.performance)
    joined = join_contexts_to_outcomes(contexts, performance)
    if joined.empty or "entered" not in joined.columns or "context_timing" not in joined.columns:
        evaluable = pd.DataFrame()
    else:
        evaluable = joined[(joined["entered"] == True) & (joined["context_timing"] == "before_entry")].copy()  # noqa: E712
    thresholds = analyze_thresholds(
        evaluable,
        min_pass_entries=max(args.min_pass_entries, 1),
    )
    summary = build_summary(
        contexts,
        performance,
        joined,
        evaluable,
        thresholds,
        min_evaluable_entries=max(args.min_evaluable_entries, 1),
    )
    paths = write_outputs(joined, thresholds, summary, args.output)

    print("Risk context validation complete.")
    print(f"Context rows: {summary['context_rows']}")
    print(f"Outcome rows: {summary['outcome_rows']}")
    print(f"Joined rows: {summary['joined_rows']}")
    print(f"Evaluable entries: {summary['evaluable_entries']}")
    print(f"Actionable: {summary['actionable']}")
    print(f"HTML: {paths['html']}")
    return 0


def load_contexts(risk_dir: Path) -> pd.DataFrame:
    files = sorted(path for path in risk_dir.glob("risk_context_*.csv") if path.name != "latest.csv") if risk_dir.exists() else []
    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            frame = pd.read_csv(path, dtype=str).fillna("")
        except (OSError, pd.errors.ParserError):
            continue
        if frame.empty or "ticker" not in frame.columns or "collected_at" not in frame.columns:
            continue
        frame["source_path"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    contexts = pd.concat(frames, ignore_index=True)
    contexts["ticker"] = contexts["ticker"].map(normalize_ticker)
    contexts = contexts[contexts["ticker"] != ""].copy()
    contexts["collected_dt"] = pd.to_datetime(contexts["collected_at"], errors="coerce", utc=True).dt.tz_convert(KST)
    contexts = contexts[contexts["collected_dt"].notna()].copy()
    contexts["date_compact"] = contexts["collected_dt"].dt.strftime("%Y%m%d")
    contexts["context_time"] = contexts["collected_dt"].dt.strftime("%H%M%S")
    for column in FEATURES:
        if column in contexts.columns:
            contexts[column] = pd.to_numeric(contexts[column], errors="coerce")
    return contexts.sort_values(["date_compact", "ticker", "collected_dt"]).reset_index(drop=True)


def load_performance(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype=str).fillna("")
    if frame.empty or "ticker" not in frame.columns or "date_compact" not in frame.columns:
        return pd.DataFrame()
    frame["ticker"] = frame["ticker"].map(normalize_ticker)
    frame = frame[frame["ticker"] != ""].copy()
    frame["entered"] = frame.get("entered", "").astype(str).str.lower().eq("true")
    frame["success"] = frame.get("success", "").astype(str).str.lower().eq("true")
    frame["failure_exit"] = frame.get("failure_exit", "").astype(str).str.lower().eq("true")
    frame["time_exit"] = frame.get("time_exit", "").astype(str).str.lower().eq("true")
    frame["net_return_pct"] = pd.to_numeric(frame.get("net_return_pct", 0.0), errors="coerce").fillna(0.0)
    frame["entry_time"] = frame.get("entry_time", "").astype(str).map(compact_time)
    frame["outcome_group"] = frame.apply(outcome_group, axis=1)
    return frame.sort_values(["date_compact", "ticker"]).reset_index(drop=True)


def join_contexts_to_outcomes(contexts: pd.DataFrame, performance: pd.DataFrame) -> pd.DataFrame:
    if contexts.empty or performance.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, outcome in performance.iterrows():
        ticker = str(outcome["ticker"])
        date = str(outcome["date_compact"])
        matches = contexts[(contexts["ticker"] == ticker) & (contexts["date_compact"] == date)].copy()
        if matches.empty:
            continue
        chosen, timing = choose_context(matches, outcome)
        row = {}
        for key, value in outcome.items():
            row[key] = value
        for key, value in chosen.items():
            row[f"context_{key}" if key in row else key] = value
        row["context_timing"] = timing
        rows.append(row)
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(
        columns=[
            "date",
            "date_compact",
            "ticker",
            "company",
            "entered",
            "outcome",
            "exit_reason",
            "net_return_pct",
            "context_timing",
        ]
    )


def choose_context(matches: pd.DataFrame, outcome: pd.Series) -> tuple[pd.Series, str]:
    if bool(outcome.get("entered")) and str(outcome.get("entry_time", "")):
        entry_time = str(outcome.get("entry_time", ""))
        before = matches[matches["context_time"].astype(str) <= entry_time].copy()
        if not before.empty:
            return before.sort_values("collected_dt").iloc[-1], "before_entry"
        return matches.sort_values("collected_dt").iloc[0], "after_entry"
    return matches.sort_values("collected_dt").iloc[-1], "no_entry_observation"


def analyze_thresholds(evaluable: pd.DataFrame, *, min_pass_entries: int) -> pd.DataFrame:
    if evaluable.empty:
        return pd.DataFrame(columns=threshold_columns())
    rows: list[dict[str, object]] = []
    total = len(evaluable)
    baseline_bad = bad_rate(evaluable)
    baseline_success = success_rate(evaluable)
    for feature, direction in FEATURES.items():
        column = feature if feature in evaluable.columns else f"context_{feature}"
        if column not in evaluable.columns:
            continue
        values = pd.to_numeric(evaluable[column], errors="coerce").dropna()
        if values.empty:
            continue
        candidates = sorted(set(values.quantile([0.25, 0.5, 0.75]).round(4).tolist() + values.round(4).unique().tolist()))
        for threshold in candidates:
            if direction == "high":
                kept = evaluable[pd.to_numeric(evaluable[column], errors="coerce") >= threshold].copy()
                rule = f"{feature} >= {threshold:g}"
            else:
                kept = evaluable[pd.to_numeric(evaluable[column], errors="coerce") <= threshold].copy()
                rule = f"{feature} <= {threshold:g}"
            if len(kept) < min_pass_entries:
                continue
            rows.append(
                {
                    "feature": feature,
                    "direction": direction,
                    "rule": rule,
                    "baseline_entries": total,
                    "kept_entries": len(kept),
                    "coverage_pct": round(len(kept) / total * 100.0, 2) if total else 0.0,
                    "baseline_success_rate_pct": baseline_success,
                    "kept_success_rate_pct": success_rate(kept),
                    "baseline_failure_or_time_rate_pct": baseline_bad,
                    "kept_failure_or_time_rate_pct": bad_rate(kept),
                    "failure_or_time_reduction_pp": round(baseline_bad - bad_rate(kept), 2),
                    "avg_net_return_pct": round(float(kept["net_return_pct"].mean()), 3) if not kept.empty else 0.0,
                }
            )
    if not rows:
        return pd.DataFrame(columns=threshold_columns())
    return pd.DataFrame(rows).sort_values(
        ["failure_or_time_reduction_pp", "kept_success_rate_pct", "kept_entries"],
        ascending=[False, False, False],
    )


def build_summary(
    contexts: pd.DataFrame,
    performance: pd.DataFrame,
    joined: pd.DataFrame,
    evaluable: pd.DataFrame,
    thresholds: pd.DataFrame,
    *,
    min_evaluable_entries: int,
) -> dict[str, object]:
    actionable = len(evaluable) >= min_evaluable_entries and not thresholds.empty
    baseline = {
        "entries": int(len(evaluable)),
        "successes": int(evaluable["success"].sum()) if not evaluable.empty else 0,
        "failure_exits": int(evaluable["failure_exit"].sum()) if not evaluable.empty else 0,
        "time_exits": int(evaluable["time_exit"].sum()) if not evaluable.empty else 0,
        "success_rate_pct": success_rate(evaluable),
        "failure_or_time_rate_pct": bad_rate(evaluable),
        "avg_net_return_pct": round(float(evaluable["net_return_pct"].mean()), 3) if not evaluable.empty else 0.0,
    }
    return {
        "generated_at": datetime.now(tz=KST).isoformat(),
        "context_rows": int(len(contexts)),
        "context_days": int(contexts["date_compact"].nunique()) if not contexts.empty else 0,
        "outcome_rows": int(len(performance)),
        "joined_rows": int(len(joined)),
        "evaluable_entries": int(len(evaluable)),
        "min_evaluable_entries": int(min_evaluable_entries),
        "actionable": bool(actionable),
        "baseline": baseline,
        "best_rules": thresholds.head(10).to_dict(orient="records") if not thresholds.empty else [],
        "limitation": limitation_text(len(evaluable), min_evaluable_entries),
    }


def write_outputs(joined: pd.DataFrame, thresholds: pd.DataFrame, summary: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    joined_csv = output_dir / f"risk_context_joined_{stamp}.csv"
    thresholds_csv = output_dir / f"risk_context_thresholds_{stamp}.csv"
    json_path = output_dir / f"risk_context_validation_{stamp}.json"
    html_path = output_dir / f"risk_context_validation_{stamp}.html"
    latest_joined = output_dir / "latest_joined.csv"
    latest_thresholds = output_dir / "latest_thresholds.csv"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"

    joined.to_csv(joined_csv, index=False, encoding="utf-8-sig")
    joined.to_csv(latest_joined, index=False, encoding="utf-8-sig")
    thresholds.to_csv(thresholds_csv, index=False, encoding="utf-8-sig")
    thresholds.to_csv(latest_thresholds, index=False, encoding="utf-8-sig")
    payload = json.dumps(summary, ensure_ascii=True, indent=2)
    json_path.write_text(payload, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    html = render_html(summary, joined, thresholds)
    html_path.write_text(html, encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8")
    return {"joined_csv": joined_csv, "thresholds_csv": thresholds_csv, "json": json_path, "html": html_path}


def render_html(summary: dict[str, object], joined: pd.DataFrame, thresholds: pd.DataFrame) -> str:
    joined_view = select_existing(
        joined,
        [
            "date",
            "ticker",
            "company",
            "entered",
            "outcome",
            "exit_reason",
            "net_return_pct",
            "context_timing",
            "spread_pct",
            "bid_ask_imbalance_10",
            "trade_strength",
            "index_change_pct",
            "risk_flag_count",
        ],
    )
    thresholds_view = thresholds.head(20)
    joined_table = joined_view.to_html(index=False, escape=True) if not joined_view.empty else "<p>조인된 행이 없습니다.</p>"
    threshold_table = thresholds_view.to_html(index=False, escape=True) if not thresholds_view.empty else "<p>평가 가능한 임계값 결과가 없습니다.</p>"
    baseline = summary["baseline"]
    status = "판단 가능" if summary["actionable"] else "표본 부족"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Risk Context Validation</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    .warn {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 12px 14px; border-radius: 8px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 12px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: right; }}
    th:nth-child(1), td:nth-child(1), th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot Risk Context Validation</h1>
  <p>Generated at {summary["generated_at"]}</p>
  <div class="warn"><strong>{status}</strong>: {summary["limitation"]}</div>
  <div class="grid">
    {card("컨텍스트 행", summary["context_rows"])}
    {card("조인 행", summary["joined_rows"])}
    {card("평가 진입", summary["evaluable_entries"])}
    {card("성공률", str(baseline["success_rate_pct"]) + "%")}
  </div>
  <div class="grid">
    {card("실패+시간청산", str(baseline["failure_or_time_rate_pct"]) + "%")}
    {card("실패철수", baseline["failure_exits"])}
    {card("시간청산", baseline["time_exits"])}
    {card("평균 순수익률", str(baseline["avg_net_return_pct"]) + "%")}
  </div>
  <h2>임계값 후보</h2>
  {threshold_table}
  <h2>조인 결과</h2>
  {joined_table}
</body>
</html>"""


def select_existing(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    existing = [column for column in columns if column in frame.columns]
    return frame[existing].copy() if existing else pd.DataFrame()


def card(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div></div>'


def outcome_group(row: pd.Series) -> str:
    if bool(row.get("success")):
        return "success"
    if bool(row.get("failure_exit")) or bool(row.get("time_exit")):
        return "failure_or_time"
    if bool(row.get("entered")):
        return "other_entered"
    return "no_entry"


def success_rate(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return round(float(frame["success"].sum()) / len(frame) * 100.0, 2)


def bad_rate(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    bad = frame["failure_exit"].astype(bool) | frame["time_exit"].astype(bool)
    return round(float(bad.sum()) / len(frame) * 100.0, 2)


def threshold_columns() -> list[str]:
    return [
        "feature",
        "direction",
        "rule",
        "baseline_entries",
        "kept_entries",
        "coverage_pct",
        "baseline_success_rate_pct",
        "kept_success_rate_pct",
        "baseline_failure_or_time_rate_pct",
        "kept_failure_or_time_rate_pct",
        "failure_or_time_reduction_pp",
        "avg_net_return_pct",
    ]


def limitation_text(evaluable_entries: int, min_evaluable_entries: int) -> str:
    if evaluable_entries == 0:
        return "진입시간 이전에 수집된 R1 컨텍스트와 실제 진입 결과가 아직 연결되지 않았습니다."
    if evaluable_entries < min_evaluable_entries:
        return f"평가 가능한 진입 표본이 {evaluable_entries}건입니다. 최소 {min_evaluable_entries}건 이상부터 임계값 후보를 참고할 수 있습니다."
    return "최소 표본 기준은 충족했지만, 자동 차단 규칙 승격 전에는 30~50건 이상으로 재검증하는 편이 안전합니다."


def normalize_ticker(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ""
    ticker = digits.zfill(6)
    return ticker if len(ticker) == 6 else ""


def compact_time(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[:6]


if __name__ == "__main__":
    raise SystemExit(main())
