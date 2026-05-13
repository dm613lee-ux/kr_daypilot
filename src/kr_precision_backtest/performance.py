from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


KST = ZoneInfo("Asia/Seoul")
TARGET_SUCCESS_RATE = 90.0

TRADE_COLUMNS = [
    "date",
    "date_compact",
    "ticker",
    "company",
    "market",
    "is_pipeline_candidate",
    "candidate_rank",
    "signal_score",
    "reference_day",
    "collection_status",
    "collection_rows",
    "collection_quality_status",
    "collection_quality_note",
    "exit_reason",
    "outcome",
    "entered",
    "success",
    "failure_exit",
    "time_exit",
    "no_signal",
    "entry_time",
    "entry_price",
    "target_price",
    "stop_price",
    "exit_time",
    "exit_price",
    "gross_return_pct",
    "net_return_pct",
    "opening_high",
    "opening_low",
    "entry_vwap",
    "note",
    "first_seen_at",
    "last_seen_at",
]


def update_performance_store(
    intraday_results: pd.DataFrame,
    collection_rows: list[dict[str, object]],
    candidate_results_path: Path,
    store_path: Path,
) -> pd.DataFrame:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_store(store_path)
    now = datetime.now(tz=KST).isoformat(timespec="seconds")
    candidates = _read_candidate_metadata(candidate_results_path)
    collection_by_ticker = _collection_by_ticker(collection_rows)
    current_date = datetime.now(tz=KST).strftime("%Y%m%d")
    current_candidate_keys = {f"{current_date}:{ticker}" for ticker in collection_by_ticker}
    ineligible_current_keys = {
        f"{current_date}:{ticker}"
        for ticker, row in collection_by_ticker.items()
        if row.get("performance_eligible") is False
    }
    existing_candidate_keys = _existing_pipeline_candidate_keys(existing)
    existing_meta = _existing_metadata(existing)

    new_rows = []
    for _, row in intraday_results.iterrows():
        ticker = str(row.get("ticker", "")).zfill(6)
        date_compact = _compact_date(row.get("date", ""))
        key = f"{date_compact}:{ticker}"
        metadata = candidates.get(ticker, {})
        if not metadata:
            metadata = existing_meta.get(key, {})
        collection = collection_by_ticker.get(ticker, {}) if date_compact == current_date else {}
        first_seen = str(existing_meta.get(key, {}).get("first_seen_at", now) or now)
        new_rows.append(
            _trade_row(
                row=row,
                ticker=ticker,
                date_compact=date_compact,
                metadata=metadata,
                collection=collection,
                is_pipeline_candidate=key in current_candidate_keys or key in existing_candidate_keys,
                first_seen_at=first_seen,
                last_seen_at=now,
            )
        )

    if not new_rows:
        store = existing
    else:
        new_frame = pd.DataFrame(new_rows, columns=TRADE_COLUMNS)
        if existing.empty:
            store = new_frame
        else:
            new_keys = set(new_frame["date_compact"].astype(str) + ":" + new_frame["ticker"].astype(str))
            old_keep = existing[
                ~((existing["date_compact"].astype(str) + ":" + existing["ticker"].astype(str)).isin(new_keys))
            ].copy()
            store = pd.concat([old_keep, new_frame], ignore_index=True)

    if ineligible_current_keys and not store.empty:
        store_keys = store["date_compact"].astype(str) + ":" + store["ticker"].astype(str).str.zfill(6)
        store = store[~store_keys.isin(ineligible_current_keys)].copy()

    store = _normalize_store(store)
    store.to_csv(store_path, index=False, encoding="utf-8-sig")
    return store


def write_performance_dashboard(store: pd.DataFrame, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    summary = summarize_performance(store)
    html = render_dashboard(store, summary)
    html_path = output_dir / f"performance_dashboard_{stamp}.html"
    json_path = output_dir / f"performance_summary_{stamp}.json"
    latest_html = output_dir / "latest.html"
    latest_json = output_dir / "latest_summary.json"
    html_path.write_text(html, encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8")
    payload = {"generated_at": datetime.now(tz=KST).isoformat(), "summary": summary}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"html": html_path, "json": json_path, "latest_html": latest_html, "latest_json": latest_json}


def summarize_performance(store: pd.DataFrame) -> dict[str, object]:
    if store.empty:
        return _empty_summary()
    candidates = store[_as_bool(store["is_pipeline_candidate"])].copy()
    manual = store[~_as_bool(store["is_pipeline_candidate"])].copy()
    if candidates.empty:
        summary = _empty_summary()
        summary["manual_or_other_rows"] = int(len(manual))
        return summary

    entered = candidates[_as_bool(candidates["entered"])].copy()
    successes = int(_as_bool(candidates["success"]).sum())
    failure_exits = int(_as_bool(candidates["failure_exit"]).sum())
    time_exits = int(_as_bool(candidates["time_exit"]).sum())
    no_signals = int(_as_bool(candidates["no_signal"]).sum())
    total = int(len(candidates))
    entered_count = int(len(entered))
    avg_net = float(entered["net_return_pct"].mean()) if entered_count else 0.0
    median_net = float(entered["net_return_pct"].median()) if entered_count else 0.0
    recent5 = _recent_window_summary(candidates, 5)
    recent20 = _recent_window_summary(candidates, 20)

    candidate_success_rate = _pct(successes, total)
    entry_success_rate = _pct(successes, entered_count)
    failure_exit_rate = _pct(failure_exits, entered_count)
    verdict = _verdict(total, entered_count, candidate_success_rate, failure_exit_rate, avg_net)
    return {
        "verdict": verdict,
        "target_success_rate": TARGET_SUCCESS_RATE,
        "target_gap_pct": round(TARGET_SUCCESS_RATE - candidate_success_rate, 2),
        "pipeline_candidates": total,
        "manual_or_other_rows": int(len(manual)),
        "entered": entered_count,
        "successes": successes,
        "failure_exits": failure_exits,
        "time_exits": time_exits,
        "no_signals": no_signals,
        "candidate_success_rate": candidate_success_rate,
        "entry_success_rate": entry_success_rate,
        "failure_exit_rate": failure_exit_rate,
        "no_signal_rate": _pct(no_signals, total),
        "avg_net_return_pct": round(avg_net, 3),
        "median_net_return_pct": round(median_net, 3),
        "max_consecutive_unsuccessful_entries": _max_consecutive_unsuccessful_entries(entered),
        "first_date": _display_date(candidates["date_compact"].min()),
        "last_date": _display_date(candidates["date_compact"].max()),
        "recent_5_days": recent5,
        "recent_20_days": recent20,
    }


def render_dashboard(store: pd.DataFrame, summary: dict[str, object]) -> str:
    generated = datetime.now(tz=KST).isoformat(timespec="seconds")
    candidates = store[_as_bool(store["is_pipeline_candidate"])].copy() if not store.empty else pd.DataFrame()
    recent_table = _recent_trades_table(candidates)
    reason_table = _reason_table(candidates)
    ticker_table = _ticker_table(candidates)
    progress = min(max(float(summary.get("candidate_success_rate", 0.0)) / TARGET_SUCCESS_RATE * 100.0, 0.0), 100.0)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KR DayPilot 누적 성과 대시보드</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d7dde5;
      --text: #202633;
      --muted: #667085;
      --green: #087443;
      --red: #b42318;
      --amber: #b54708;
      --blue: #175cd3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", sans-serif;
      line-height: 1.5;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 44px; }}
    h1 {{ font-size: 28px; margin: 0 0 6px; }}
    h2 {{ font-size: 19px; margin: 28px 0 12px; }}
    .muted {{ color: var(--muted); }}
    .hero {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      margin-bottom: 18px;
    }}
    .verdict {{
      display: inline-block;
      margin-top: 14px;
      padding: 8px 12px;
      border-radius: 6px;
      background: #fff7ed;
      color: var(--amber);
      font-weight: 700;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 15px 16px;
      min-height: 106px;
    }}
    .label {{ color: var(--muted); font-size: 13px; }}
    .value {{ font-size: 27px; font-weight: 750; margin-top: 8px; letter-spacing: 0; }}
    .good {{ color: var(--green); }}
    .bad {{ color: var(--red); }}
    .warn {{ color: var(--amber); }}
    .progress {{
      height: 14px;
      border-radius: 999px;
      background: #e5e7eb;
      overflow: hidden;
      margin-top: 12px;
    }}
    .progress div {{ width: {progress:.1f}%; height: 100%; background: var(--blue); }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      font-size: 13px;
    }}
    th, td {{ border-bottom: 1px solid #eef1f5; padding: 9px 10px; text-align: right; }}
    th {{ color: var(--muted); font-weight: 650; background: #fbfcfd; }}
    th:first-child, td:first-child,
    th:nth-child(3), td:nth-child(3),
    th:nth-child(4), td:nth-child(4) {{ text-align: left; }}
    tr:last-child td {{ border-bottom: 0; }}
    .section-note {{ margin: -4px 0 12px; color: var(--muted); font-size: 14px; }}
    .empty {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; color: var(--muted); }}
    @media (max-width: 860px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      main {{ padding: 20px 12px 34px; }}
      .grid {{ grid-template-columns: 1fr; }}
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
    }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <h1>KR DayPilot 누적 성과 대시보드</h1>
    <div class="muted">생성 시각: {escape(generated)} · 기준: 자동 파이프라인 추천 후보만 기본 성과에 포함</div>
    <div class="verdict">실전 판정: {escape(str(summary.get("verdict", "")))}</div>
    <div class="progress" aria-label="90% 목표 대비 진행률"><div></div></div>
    <div class="muted">후보 기준 성공률 {summary.get("candidate_success_rate", 0)}% / 목표 {TARGET_SUCCESS_RATE:.0f}%</div>
  </section>

  <section class="grid">
    {_metric("자동 추천 후보", summary.get("pipeline_candidates", 0))}
    {_metric("후보 기준 성공률", f'{summary.get("candidate_success_rate", 0)}%', _rate_class(summary.get("candidate_success_rate", 0)))}
    {_metric("실제 진입 기준 성공률", f'{summary.get("entry_success_rate", 0)}%', _rate_class(summary.get("entry_success_rate", 0)))}
    {_metric("실패철수율", f'{summary.get("failure_exit_rate", 0)}%', "bad" if float(summary.get("failure_exit_rate", 0)) > 20 else "")}
    {_metric("실제 진입", summary.get("entered", 0))}
    {_metric("무진입률", f'{summary.get("no_signal_rate", 0)}%', "warn" if float(summary.get("no_signal_rate", 0)) > 40 else "")}
    {_metric("평균 순수익률", f'{summary.get("avg_net_return_pct", 0)}%', "good" if float(summary.get("avg_net_return_pct", 0)) > 0 else "bad")}
    {_metric("최대 연속 미성공", summary.get("max_consecutive_unsuccessful_entries", 0))}
  </section>

  <h2>최근 추천 결과</h2>
  <p class="section-note">성공률 판단은 평균수익률보다 추천 후보 중 실제 목표 도달 종목 수를 우선해서 봅니다.</p>
  {recent_table}

  <h2>실패·무진입 원인</h2>
  <p class="section-note">반복 원인이 쌓이면 다음 전략 개선 대상입니다.</p>
  {reason_table}

  <h2>종목별 누적 성과</h2>
  {ticker_table}
</main>
</body>
</html>"""


def _trade_row(
    *,
    row: pd.Series,
    ticker: str,
    date_compact: str,
    metadata: dict[str, object],
    collection: dict[str, object],
    is_pipeline_candidate: bool,
    first_seen_at: str,
    last_seen_at: str,
) -> dict[str, object]:
    exit_reason = str(row.get("exit_reason", ""))
    entered = exit_reason != "no_signal" and _float(row.get("entry_price", 0.0)) > 0
    success = exit_reason == "target_hit"
    failure_exit = exit_reason in {"stop_loss", "ambiguous_stop_first"}
    time_exit = exit_reason == "time_exit"
    no_signal = exit_reason == "no_signal"
    return {
        "date": _display_date(date_compact),
        "date_compact": date_compact,
        "ticker": ticker,
        "company": str(metadata.get("company", "")),
        "market": str(metadata.get("market", "")),
        "is_pipeline_candidate": bool(is_pipeline_candidate),
        "candidate_rank": metadata.get("rank", ""),
        "signal_score": metadata.get("signal_score", ""),
        "reference_day": metadata.get("reference_day", ""),
        "collection_status": collection.get("status", ""),
        "collection_rows": collection.get("rows", ""),
        "collection_quality_status": collection.get("data_quality_status", ""),
        "collection_quality_note": collection.get("data_quality_note", ""),
        "exit_reason": exit_reason,
        "outcome": _outcome_label(exit_reason),
        "entered": entered,
        "success": success,
        "failure_exit": failure_exit,
        "time_exit": time_exit,
        "no_signal": no_signal,
        "entry_time": row.get("entry_time", ""),
        "entry_price": row.get("entry_price", 0.0),
        "target_price": row.get("target_price", 0.0),
        "stop_price": row.get("stop_price", 0.0),
        "exit_time": row.get("exit_time", ""),
        "exit_price": row.get("exit_price", 0.0),
        "gross_return_pct": row.get("gross_return_pct", 0.0),
        "net_return_pct": row.get("net_return_pct", 0.0),
        "opening_high": row.get("opening_high", 0.0),
        "opening_low": row.get("opening_low", 0.0),
        "entry_vwap": row.get("entry_vwap", 0.0),
        "note": row.get("note", ""),
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
    }


def _read_store(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = pd.read_csv(path, dtype={"ticker": str, "date_compact": str})
    return _normalize_store(frame)


def _normalize_store(frame: pd.DataFrame) -> pd.DataFrame:
    for column in TRADE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[TRADE_COLUMNS].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame["date_compact"] = frame["date_compact"].astype(str).map(_compact_date)
    frame["date"] = frame["date_compact"].map(_display_date)
    for column in [
        "candidate_rank",
        "signal_score",
        "collection_rows",
        "entry_price",
        "target_price",
        "stop_price",
        "exit_price",
        "gross_return_pct",
        "net_return_pct",
        "opening_high",
        "opening_low",
        "entry_vwap",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    for column in ["is_pipeline_candidate", "entered", "success", "failure_exit", "time_exit", "no_signal"]:
        frame[column] = _as_bool(frame[column])
    return frame.sort_values(["date_compact", "ticker"]).reset_index(drop=True)


def _read_candidate_metadata(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype={"ticker": str})
    if frame.empty or "ticker" not in frame.columns:
        return {}
    if "reference_day" in frame.columns:
        frame = frame[frame["reference_day"].astype(str) == frame["reference_day"].astype(str).max()].copy()
    meta = {}
    for _, row in frame.iterrows():
        ticker = str(row.get("ticker", "")).zfill(6)
        meta[ticker] = {
            "company": row.get("company", ""),
            "market": row.get("market", ""),
            "rank": row.get("rank", ""),
            "signal_score": row.get("signal_score", ""),
            "reference_day": row.get("reference_day", ""),
        }
    return meta


def _collection_by_ticker(collection_rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    rows = {}
    for row in collection_rows:
        ticker = str(row.get("ticker", "")).zfill(6)
        if ticker and len(ticker) == 6:
            rows[ticker] = row
    return rows


def _existing_pipeline_candidate_keys(existing: pd.DataFrame) -> set[str]:
    if existing.empty:
        return set()
    candidates = existing[_as_bool(existing["is_pipeline_candidate"])]
    return set(candidates["date_compact"].astype(str) + ":" + candidates["ticker"].astype(str))


def _existing_metadata(existing: pd.DataFrame) -> dict[str, dict[str, object]]:
    if existing.empty:
        return {}
    result = {}
    for _, row in existing.iterrows():
        key = f"{row.get('date_compact', '')}:{row.get('ticker', '')}"
        result[key] = {
            "company": row.get("company", ""),
            "market": row.get("market", ""),
            "rank": row.get("candidate_rank", ""),
            "signal_score": row.get("signal_score", ""),
            "reference_day": row.get("reference_day", ""),
            "first_seen_at": row.get("first_seen_at", ""),
        }
    return result


def _recent_window_summary(candidates: pd.DataFrame, days: int) -> dict[str, object]:
    if candidates.empty:
        return {"days": 0, "candidates": 0, "candidate_success_rate": 0.0, "entry_success_rate": 0.0}
    unique_dates = sorted(candidates["date_compact"].dropna().astype(str).unique())
    selected_dates = unique_dates[-days:]
    window = candidates[candidates["date_compact"].astype(str).isin(selected_dates)]
    entered = window[_as_bool(window["entered"])]
    successes = int(_as_bool(window["success"]).sum())
    return {
        "days": len(selected_dates),
        "candidates": int(len(window)),
        "candidate_success_rate": _pct(successes, len(window)),
        "entry_success_rate": _pct(successes, len(entered)),
    }


def _max_consecutive_unsuccessful_entries(entered: pd.DataFrame) -> int:
    if entered.empty:
        return 0
    ordered = entered.sort_values(["date_compact", "entry_time", "ticker"])
    streak = 0
    max_streak = 0
    for success in _as_bool(ordered["success"]):
        if success:
            streak = 0
        else:
            streak += 1
            max_streak = max(max_streak, streak)
    return max_streak


def _recent_trades_table(candidates: pd.DataFrame) -> str:
    if candidates.empty:
        return '<div class="empty">아직 자동 추천 후보 누적 결과가 없습니다.</div>'
    columns = [
        "date",
        "ticker",
        "company",
        "outcome",
        "candidate_rank",
        "signal_score",
        "entry_price",
        "target_price",
        "stop_price",
        "exit_price",
        "net_return_pct",
        "note",
    ]
    labels = {
        "date": "날짜",
        "ticker": "종목코드",
        "company": "종목명",
        "outcome": "결과",
        "candidate_rank": "추천순위",
        "signal_score": "신호점수",
        "entry_price": "진입가",
        "target_price": "목표가",
        "stop_price": "손절가",
        "exit_price": "청산가",
        "net_return_pct": "순수익률",
        "note": "비고",
    }
    recent = candidates.sort_values(["date_compact", "candidate_rank", "ticker"], ascending=[False, True, True]).head(30)
    return _data_table(recent[columns], labels)


def _reason_table(candidates: pd.DataFrame) -> str:
    if candidates.empty:
        return '<div class="empty">집계할 원인이 없습니다.</div>'
    grouped = (
        candidates.assign(reason=candidates["note"].where(candidates["note"].astype(str) != "", candidates["exit_reason"]))
        .groupby(["outcome", "reason"], dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .head(20)
    )
    return _data_table(grouped, {"outcome": "결과", "reason": "원인", "count": "건수"})


def _ticker_table(candidates: pd.DataFrame) -> str:
    if candidates.empty:
        return '<div class="empty">종목별 집계가 없습니다.</div>'
    grouped = (
        candidates.groupby(["ticker", "company"], dropna=False)
        .agg(
            candidates=("ticker", "size"),
            entered=("entered", lambda value: int(_as_bool(value).sum())),
            successes=("success", lambda value: int(_as_bool(value).sum())),
            avg_net_return_pct=("net_return_pct", "mean"),
        )
        .reset_index()
    )
    grouped["candidate_success_rate"] = grouped.apply(lambda row: _pct(row["successes"], row["candidates"]), axis=1)
    grouped = grouped.sort_values(["successes", "candidate_success_rate", "candidates"], ascending=False).head(30)
    return _data_table(
        grouped,
        {
            "ticker": "종목코드",
            "company": "종목명",
            "candidates": "후보수",
            "entered": "진입수",
            "successes": "성공수",
            "avg_net_return_pct": "평균순수익률",
            "candidate_success_rate": "후보성공률",
        },
    )


def _data_table(frame: pd.DataFrame, labels: dict[str, str]) -> str:
    if frame.empty:
        return '<div class="empty">표시할 데이터가 없습니다.</div>'
    headers = "".join(f"<th>{escape(labels.get(column, column))}</th>" for column in frame.columns)
    body_rows = []
    for _, row in frame.iterrows():
        cells = "".join(f"<td>{escape(_format_cell(row[column]))}</td>" for column in frame.columns)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{headers}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _metric(label: str, value: object, klass: str = "") -> str:
    return (
        f'<div class="card"><div class="label">{escape(str(label))}</div>'
        f'<div class="value {escape(klass)}">{escape(str(value))}</div></div>'
    )


def _rate_class(value: object) -> str:
    numeric = _float(value)
    if numeric >= TARGET_SUCCESS_RATE:
        return "good"
    if numeric >= 50.0:
        return "warn"
    return "bad"


def _verdict(total: int, entered: int, candidate_success_rate: float, failure_exit_rate: float, avg_net: float) -> str:
    if total < 50 or entered < 30:
        return "표본 부족 - 관찰 계속"
    if candidate_success_rate >= TARGET_SUCCESS_RATE and failure_exit_rate <= 10.0 and avg_net > 0:
        return "90% 기준 충족 후보"
    if candidate_success_rate >= 60.0 and failure_exit_rate <= 25.0 and avg_net > 0:
        return "개선 후 실전 후보"
    return "실전 사용 보류 - 전략 개선 필요"


def _empty_summary() -> dict[str, object]:
    return {
        "verdict": "표본 부족 - 관찰 계속",
        "target_success_rate": TARGET_SUCCESS_RATE,
        "target_gap_pct": TARGET_SUCCESS_RATE,
        "pipeline_candidates": 0,
        "manual_or_other_rows": 0,
        "entered": 0,
        "successes": 0,
        "failure_exits": 0,
        "time_exits": 0,
        "no_signals": 0,
        "candidate_success_rate": 0.0,
        "entry_success_rate": 0.0,
        "failure_exit_rate": 0.0,
        "no_signal_rate": 0.0,
        "avg_net_return_pct": 0.0,
        "median_net_return_pct": 0.0,
        "max_consecutive_unsuccessful_entries": 0,
        "first_date": "",
        "last_date": "",
        "recent_5_days": {},
        "recent_20_days": {},
    }


def _outcome_label(exit_reason: str) -> str:
    labels = {
        "target_hit": "성공",
        "stop_loss": "실패철수",
        "ambiguous_stop_first": "실패철수",
        "time_exit": "시간청산",
        "no_signal": "무진입",
    }
    return labels.get(exit_reason, exit_reason or "미확인")


def _compact_date(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text[:8] if len(text) >= 8 else text


def _display_date(value: object) -> str:
    text = _compact_date(value)
    if len(text) != 8:
        return text
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _pct(numerator: int | float, denominator: int | float) -> float:
    denominator = float(denominator)
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / denominator * 100.0, 2)


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_bool(series: object) -> pd.Series:
    if isinstance(series, pd.Series):
        if series.dtype == bool:
            return series.fillna(False)
        return series.astype(str).str.lower().isin(["true", "1", "yes", "y"])
    return pd.Series([str(series).lower() in ["true", "1", "yes", "y"]])


def _format_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)
