from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import pandas as pd

from .collect_intraday import _collect_ticker_bars, _resolve_tickers, _write_bars
from .collect_risk_context import collect_risk_context_for_candidates, resolve_candidates as resolve_risk_candidates
from .data import add_daily_proxy_features, load_price_history
from .env_config import load_kis_credentials
from .intraday_strategy import evaluate_intraday_day
from .kis_client import KisApiError, KisClient
from .performance import update_performance_store, write_performance_dashboard
from .policy import load_policy
from .report import write_outputs as write_daily_proxy_outputs
from .simulator import BacktestOptions, run_backtest


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_ENV = PROGRAM_ROOT / ".env"
DEFAULT_TOKEN_CACHE = PROGRAM_ROOT / "runtime" / "kis_token.json"
DEFAULT_DAILY_OUTPUT = PROGRAM_ROOT / "output"
DEFAULT_INTRADAY_ROOT = PROGRAM_ROOT / "data" / "intraday" / "minute_bars"
DEFAULT_PIPELINE_OUTPUT = PROGRAM_ROOT / "output" / "daily_pipeline"
DEFAULT_INTRADAY_OUTPUT = PROGRAM_ROOT / "output" / "intraday"
DEFAULT_PERFORMANCE_STORE = PROGRAM_ROOT / "data" / "performance" / "trade_log.csv"
DEFAULT_DASHBOARD_OUTPUT = PROGRAM_ROOT / "output" / "dashboard"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_LIVE_CONTEXT_ROOT = PROGRAM_ROOT / "data" / "live_context"
DEFAULT_RISK_CONTEXT_OUTPUT = PROGRAM_ROOT / "output" / "risk_context"
DEFAULT_REFERENCE_DIR = PROGRAM_ROOT / "data" / "reference"
DEFAULT_LOCK = PROGRAM_ROOT / "runtime" / "locks" / "daily_pipeline.lock"
PERFORMANCE_QUALITY_STATUSES = {"complete_after_close"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KR DayPilot daily data collection and validation pipeline.")
    parser.add_argument("--tickers", default="", help="Comma separated tickers. Defaults to latest daily proxy candidates.")
    parser.add_argument("--max-tickers", type=int, default=2)
    parser.add_argument("--max-reference-days", type=int, default=250)
    parser.add_argument("--input-hour", default="153000")
    parser.add_argument("--market-div", default="J")
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--force-collect", action="store_true")
    parser.add_argument("--skip-daily-proxy", action="store_true")
    parser.add_argument("--single-window", action="store_true")
    parser.add_argument("--skip-risk-context", action="store_true")
    parser.add_argument("--skip-risk-dart", action="store_true")
    parser.add_argument("--skip-risk-news", action="store_true")
    args = parser.parse_args()

    with pipeline_lock(DEFAULT_LOCK):
        policy = load_policy(DEFAULT_POLICY)
        daily_paths: dict[str, Path] = {}
        if not args.skip_daily_proxy:
            history = add_daily_proxy_features(load_price_history(DEFAULT_PRICE_HISTORY))
            daily_results, daily_summary = run_backtest(
                history,
                policy,
                BacktestOptions(max_reference_days=max(args.max_reference_days, 1)),
            )
            daily_paths = write_daily_proxy_outputs(daily_results, daily_summary, policy, DEFAULT_DAILY_OUTPUT)

        tickers = _resolve_tickers(args.tickers, DEFAULT_DAILY_OUTPUT / "latest.csv", max_tickers=max(args.max_tickers, 1))
        risk_context_rows, risk_context_paths = collect_pipeline_risk_context(
            tickers,
            market_div=args.market_div,
            sleep_seconds=max(args.sleep_seconds, 0.0),
            skip=args.skip_risk_context,
            skip_dart=args.skip_risk_dart,
            skip_news=args.skip_risk_news,
        )
        collection_rows = collect_tickers(
            tickers,
            input_hour=args.input_hour,
            market_div=args.market_div,
            sleep_seconds=max(args.sleep_seconds, 0.0),
            force_collect=args.force_collect,
            single_window=args.single_window,
        )
        collection_rows = annotate_collection_quality(collection_rows)
        intraday_results, intraday_summary = validate_intraday(DEFAULT_INTRADAY_ROOT, collection_rows)
        performance_intraday_results = filter_performance_results(intraday_results, collection_rows)
        intraday_paths = write_intraday_outputs(intraday_results, intraday_summary, DEFAULT_INTRADAY_OUTPUT)
        performance_store = update_performance_store(
            intraday_results=performance_intraday_results,
            collection_rows=collection_rows,
            candidate_results_path=DEFAULT_DAILY_OUTPUT / "latest.csv",
            store_path=DEFAULT_PERFORMANCE_STORE,
        )
        dashboard_paths = write_performance_dashboard(performance_store, DEFAULT_DASHBOARD_OUTPUT)
        pipeline_paths = write_pipeline_report(
            collection_rows=collection_rows,
            intraday_summary=intraday_summary,
            daily_paths=daily_paths,
            intraday_paths=intraday_paths,
            dashboard_paths=dashboard_paths,
            risk_context_rows=risk_context_rows,
            risk_context_paths=risk_context_paths,
            output_dir=DEFAULT_PIPELINE_OUTPUT,
        )

    print("KR DayPilot daily pipeline complete.")
    print(f"Tickers: {','.join(tickers) if tickers else 'none'}")
    print(f"Collected or reused: {sum(1 for row in collection_rows if row['status'] in {'collected', 'skipped_existing'})}")
    print(f"Intraday signals: {intraday_summary['signals']}")
    print(f"Intraday success rate: {intraday_summary['success_rate']}%")
    if risk_context_rows:
        print(f"Risk context rows: {len(risk_context_rows)}")
    print(f"HTML: {pipeline_paths['html']}")
    print(f"Dashboard: {dashboard_paths['latest_html']}")
    return 0


class pipeline_lock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            age_seconds = datetime.now(tz=KST).timestamp() - self.path.stat().st_mtime
            if age_seconds < 7200:
                raise RuntimeError(f"Pipeline is already running: {self.path}")
            self.path.unlink()
        self.path.write_text(datetime.now(tz=KST).isoformat(), encoding="utf-8")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def collect_tickers(
    tickers: list[str],
    *,
    input_hour: str,
    market_div: str,
    sleep_seconds: float,
    force_collect: bool,
    single_window: bool,
) -> list[dict[str, object]]:
    if not tickers:
        return []
    credentials = load_kis_credentials(DEFAULT_ENV)
    client = KisClient(credentials=credentials, token_cache_path=DEFAULT_TOKEN_CACHE)
    rows: list[dict[str, object]] = []
    today = datetime.now(tz=KST).strftime("%Y%m%d")

    for ticker in tickers:
        existing = DEFAULT_INTRADAY_ROOT / today / f"{ticker}.csv"
        if existing.exists() and not force_collect:
            count = _csv_row_count(existing)
            if count >= 300:
                rows.append({"ticker": ticker, "status": "skipped_existing", "rows": count, "path": str(existing)})
                continue
        try:
            bars = _collect_ticker_bars(
                client,
                ticker,
                market_div=market_div,
                input_hour=input_hour,
                single_window=single_window,
                sleep_seconds=sleep_seconds,
            )
            if bars.empty:
                rows.append({"ticker": ticker, "status": "empty", "rows": 0, "path": ""})
            else:
                path = _write_bars(bars, DEFAULT_INTRADAY_ROOT)
                rows.append({"ticker": ticker, "status": "collected", "rows": len(bars), "path": str(path)})
        except KisApiError as exc:
            rows.append({"ticker": ticker, "status": "kis_error", "rows": 0, "path": "", "error": str(exc)})
        time.sleep(sleep_seconds)
    return rows


def collect_pipeline_risk_context(
    tickers: list[str],
    *,
    market_div: str,
    sleep_seconds: float,
    skip: bool,
    skip_dart: bool,
    skip_news: bool,
) -> tuple[list[dict[str, object]], dict[str, Path]]:
    if skip:
        return [{"status": "skipped", "reason": "skip-risk-context"}], {}
    if not tickers:
        return [], {}
    try:
        candidates = resolve_risk_candidates(
            ",".join(tickers),
            DEFAULT_DAILY_OUTPUT / "latest.csv",
            DEFAULT_UNIVERSE,
            max_tickers=len(tickers),
        )
        summaries, paths = collect_risk_context_for_candidates(
            candidates,
            env_file=DEFAULT_ENV,
            token_cache_path=DEFAULT_TOKEN_CACHE,
            market_div=market_div,
            output_root=DEFAULT_LIVE_CONTEXT_ROOT,
            report_output=DEFAULT_RISK_CONTEXT_OUTPUT,
            reference_dir=DEFAULT_REFERENCE_DIR,
            sleep_seconds=sleep_seconds,
            skip_dart=skip_dart,
            skip_news=skip_news,
            echo=False,
        )
        return summaries, paths
    except Exception as exc:
        return [{"status": "error", "error": str(exc)[:300]}], {}


def annotate_collection_quality(collection_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    annotated = []
    for row in collection_rows:
        next_row = dict(row)
        quality = _collection_quality(next_row)
        next_row.update(quality)
        next_row["performance_eligible"] = quality["data_quality_status"] in PERFORMANCE_QUALITY_STATUSES
        annotated.append(next_row)
    return annotated


def validate_intraday(root: Path, collection_rows: list[dict[str, object]] | None = None) -> tuple[pd.DataFrame, dict[str, object]]:
    if collection_rows is None:
        files = sorted(path for path in root.rglob("*.csv") if "latest" not in path.parts) if root.exists() else []
    else:
        files = _collection_files(collection_rows)
    rows = []
    policy = load_policy(DEFAULT_POLICY)
    for path in files:
        bars = pd.read_csv(path, dtype={"ticker": str, "date": str, "time": str})
        rows.append(asdict(evaluate_intraday_day(bars, policy)))
    results = pd.DataFrame(rows)
    return results, _intraday_summary(results)


def filter_performance_results(intraday_results: pd.DataFrame, collection_rows: list[dict[str, object]]) -> pd.DataFrame:
    if intraday_results.empty:
        return intraday_results
    eligible_keys = {
        f"{str(row.get('date_compact', ''))}:{str(row.get('ticker', '')).zfill(6)}"
        for row in collection_rows
        if row.get("performance_eligible") is True and row.get("date_compact")
    }
    if not eligible_keys:
        return intraday_results.iloc[0:0].copy()
    frame = intraday_results.copy()
    keys = frame["date"].astype(str).str.replace("-", "", regex=False).str[:8] + ":" + frame["ticker"].astype(str).str.zfill(6)
    return frame[keys.isin(eligible_keys)].copy()


def write_intraday_outputs(results: pd.DataFrame, summary: dict[str, object], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"phase1c_intraday_{stamp}.csv"
    json_path = output_dir / f"phase1c_intraday_summary_{stamp}.json"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest_summary.json"
    results.to_csv(csv_path, index=False, encoding="utf-8-sig")
    results.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {"summary": summary}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": csv_path, "json": json_path}


def write_pipeline_report(
    *,
    collection_rows: list[dict[str, object]],
    intraday_summary: dict[str, object],
    daily_paths: dict[str, Path],
    intraday_paths: dict[str, Path],
    dashboard_paths: dict[str, Path],
    risk_context_rows: list[dict[str, object]],
    risk_context_paths: dict[str, Path],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"daily_pipeline_{stamp}.json"
    html_path = output_dir / f"daily_pipeline_{stamp}.html"
    manifest_path = output_dir / f"daily_pipeline_manifest_{stamp}.csv"
    latest_json = output_dir / "latest.json"
    latest_html = output_dir / "latest.html"
    latest_manifest = output_dir / "latest_manifest.csv"
    pd.DataFrame(collection_rows).to_csv(manifest_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(collection_rows).to_csv(latest_manifest, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at": datetime.now(tz=KST).isoformat(),
        "collection": collection_rows,
        "intraday_summary": intraday_summary,
        "daily_proxy_outputs": {key: str(value) for key, value in daily_paths.items()},
        "intraday_outputs": {key: str(value) for key, value in intraday_paths.items()},
        "dashboard_outputs": {key: str(value) for key, value in dashboard_paths.items()},
        "risk_context": risk_context_rows,
        "risk_context_outputs": {key: str(value) for key, value in risk_context_paths.items()},
        "manifest_outputs": {"csv": str(manifest_path), "latest_csv": str(latest_manifest)},
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = _render_pipeline_html(payload)
    html_path.write_text(html, encoding="utf-8")
    latest_html.write_text(html, encoding="utf-8")
    return {"json": json_path, "html": html_path, "manifest_csv": manifest_path, "latest_manifest_csv": latest_manifest}


def _intraday_summary(results: pd.DataFrame) -> dict[str, object]:
    if results.empty:
        return {"files": 0, "signals": 0, "successes": 0, "failures": 0, "time_exits": 0, "success_rate": 0.0, "avg_net_return_pct": 0.0}
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
    }


def _render_pipeline_html(payload: dict[str, object]) -> str:
    collection = pd.DataFrame(payload["collection"])
    collection_table = collection.to_html(index=False, escape=True) if not collection.empty else "<p>수집 대상이 없습니다.</p>"
    risk_context = pd.DataFrame(payload.get("risk_context", []))
    risk_context_table = risk_context.to_html(index=False, escape=True) if not risk_context.empty else "<p>Risk context rows were not collected.</p>"
    summary = payload["intraday_summary"]
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Daily Pipeline</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 28px; font-weight: 700; margin-top: 8px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot 일일 수집·검증 파이프라인</h1>
  <p>생성 시각: {payload["generated_at"]}</p>
  <div class="grid">
    {card("분봉 파일 수", summary.get("files", 0))}
    {card("신호 수", summary.get("signals", 0))}
    {card("성공률", str(summary.get("success_rate", 0)) + "%")}
    {card("평균 순수익률", str(summary.get("avg_net_return_pct", 0)) + "%")}
  </div>
  <h2>수집 상태</h2>
  {collection_table}
  <h2>Risk Context</h2>
  {risk_context_table}
  <p><a href="../risk_context/latest.html">Risk context latest report</a></p>
  <h2>누적 성과</h2>
  <p><a href="../dashboard/latest.html">누적 성과 대시보드 열기</a></p>
</body>
</html>"""


def card(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div></div>'


def _csv_row_count(path: Path) -> int:
    try:
        return max(sum(1 for _ in path.open("r", encoding="utf-8-sig")) - 1, 0)
    except OSError:
        return 0


def _collection_files(collection_rows: list[dict[str, object]]) -> list[Path]:
    files = []
    seen = set()
    for row in collection_rows:
        path_text = str(row.get("path", "") or "")
        if not path_text:
            continue
        path = Path(path_text)
        if not path.exists() or path in seen:
            continue
        files.append(path)
        seen.add(path)
    return files


def _collection_quality(row: dict[str, object]) -> dict[str, object]:
    status = str(row.get("status", "") or "")
    path_text = str(row.get("path", "") or "")
    if status not in {"collected", "skipped_existing"}:
        return {
            "data_quality_status": "not_collected",
            "data_quality_note": status or "no_status",
            "date_compact": "",
            "first_bar_time": "",
            "last_bar_time": "",
            "bar_rows": 0,
        }
    if not path_text:
        return {
            "data_quality_status": "missing_file",
            "data_quality_note": "collection row has no path",
            "date_compact": "",
            "first_bar_time": "",
            "last_bar_time": "",
            "bar_rows": 0,
        }

    path = Path(path_text)
    if not path.exists():
        return {
            "data_quality_status": "missing_file",
            "data_quality_note": str(path),
            "date_compact": "",
            "first_bar_time": "",
            "last_bar_time": "",
            "bar_rows": 0,
        }

    try:
        bars = pd.read_csv(path, dtype={"ticker": str, "date": str, "time": str})
    except Exception as exc:
        return {
            "data_quality_status": "unreadable_file",
            "data_quality_note": str(exc)[:200],
            "date_compact": "",
            "first_bar_time": "",
            "last_bar_time": "",
            "bar_rows": 0,
        }

    if bars.empty or "time" not in bars.columns:
        return {
            "data_quality_status": "no_bars",
            "data_quality_note": "empty intraday csv",
            "date_compact": "",
            "first_bar_time": "",
            "last_bar_time": "",
            "bar_rows": int(len(bars)),
        }

    times = bars["time"].astype(str).str.replace(":", "", regex=False).str.zfill(6).str[:6]
    dates = bars["date"].astype(str).str.replace("-", "", regex=False).str[:8] if "date" in bars.columns else pd.Series([], dtype=str)
    first_time = str(times.min())
    last_time = str(times.max())
    date_compact = str(dates.mode().iloc[0]) if not dates.empty else ""
    opening_count = int(((times >= "090000") & (times < "091000")).sum())
    issues = []
    if len(bars) < 300:
        issues.append("rows_lt_300")
    if opening_count == 0:
        issues.append("missing_opening_range")
    if last_time < "091000":
        issues.append("partial_before_open")
    elif last_time < "153000":
        issues.append("partial_intraday")

    if not issues:
        quality_status = "complete_after_close"
        note = "eligible for performance store"
    else:
        priority = ["partial_before_open", "missing_opening_range", "rows_lt_300", "partial_intraday"]
        quality_status = next((item for item in priority if item in issues), issues[0])
        note = ";".join(issues)

    return {
        "data_quality_status": quality_status,
        "data_quality_note": note,
        "date_compact": date_compact,
        "first_bar_time": first_time,
        "last_bar_time": last_time,
        "bar_rows": int(len(bars)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
