from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import stat
from zoneinfo import ZoneInfo

import pandas as pd

from .investment_recommender import json_ready
from .run_web_app import (
    PROGRAM_ROOT,
    load_dashboard_payload,
    summarize_paper_ledger,
    summarize_ticker_history,
)


KST = ZoneInfo("Asia/Seoul")
DEFAULT_OUTPUT = PROGRAM_ROOT / "site"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a static GitHub Pages version of KR DayPilot.")
    parser.add_argument("--program-root", type=Path, default=PROGRAM_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = build_static_site(args.program_root, args.output)
    print(f"KR DayPilot static site built: {result['output_dir']}")
    print(f"Dashboard: {result['dashboard_path']}")
    print(f"Ticker files: {result['ticker_files']}")
    return 0


def build_static_site(program_root: Path = PROGRAM_ROOT, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    program_root = program_root.resolve()
    output_dir = output_dir.resolve()
    web_root = program_root / "webapp"
    if not web_root.exists():
        raise FileNotFoundError(f"webapp directory not found: {web_root}")
    if output_dir == program_root or output_dir in program_root.parents:
        raise ValueError(f"refusing to delete unsafe static output path: {output_dir}")

    if output_dir.exists():
        remove_tree(output_dir)
    (output_dir / "data" / "tickers").mkdir(parents=True, exist_ok=True)
    (output_dir / "reports").mkdir(parents=True, exist_ok=True)

    copy_web_assets(web_root, output_dir)
    dashboard = public_dashboard_payload(program_root)
    ticker_files = write_ticker_payloads(program_root, output_dir, dashboard)
    dashboard_path = output_dir / "data" / "dashboard.json"
    dashboard_path.write_text(json.dumps(json_ready(dashboard), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    copy_reports(program_root, output_dir)

    return {
        "output_dir": str(output_dir),
        "dashboard_path": str(dashboard_path),
        "ticker_files": ticker_files,
    }


def copy_web_assets(web_root: Path, output_dir: Path) -> None:
    for name in ["app.js", "app.css"]:
        source = web_root / name
        if source.exists():
            shutil.copy2(source, output_dir / name)
    index_source = web_root / "index.html"
    if not index_source.exists():
        raise FileNotFoundError(f"index.html not found: {index_source}")
    index_html = index_source.read_text(encoding="utf-8")
    index_html = prepare_static_index(index_html)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")


def remove_tree(path: Path) -> None:
    def onerror(function: object, target: str, _exc_info: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=onerror)


def prepare_static_index(index_html: str) -> str:
    index_html = index_html.replace('href="/app.css', 'href="app.css')
    index_html = index_html.replace('src="/app.js', 'src="app.js')
    index_html = index_html.replace('href="/report/latest.html"', 'href="reports/latest.html"')
    marker = "</head>"
    config = (
        "  <script>\n"
        "    window.KR_DAYPILOT_STATIC = true;\n"
        "    window.KR_DAYPILOT_BASE = \".\";\n"
        "  </script>\n"
    )
    if "window.KR_DAYPILOT_STATIC" not in index_html and marker in index_html:
        index_html = index_html.replace(marker, config + marker, 1)
    return index_html


def public_dashboard_payload(program_root: Path) -> dict[str, object]:
    dashboard = load_dashboard_payload(program_root)
    dashboard["generated_at"] = datetime.now(tz=KST).isoformat(timespec="seconds")
    dashboard["deployment"] = {
        "mode": "github_pages_static",
        "built_at": dashboard["generated_at"],
        "personal_state": "browser_local_storage",
        "server_api": "disabled",
    }
    dashboard["user_decisions"] = {}
    dashboard["paper_ledger"] = []
    dashboard["paper_portfolio_summary"] = summarize_paper_ledger([])
    dashboard["job"] = {
        "running": False,
        "started_at": "",
        "finished_at": dashboard["generated_at"],
        "returncode": 0,
        "command": [],
        "lines": ["Static GitHub Pages build. Data refresh runs in GitHub Actions."],
    }
    dashboard["files"] = {
        "latest_report_html": "reports/latest.html" if (program_root / "output" / "investment_recommender" / "latest.html").exists() else "",
        "latest_summary_json": "data/dashboard.json",
        "core_report_html": "reports/core.html" if (program_root / "output" / "fundamental_core" / "latest.html").exists() else "",
        "core_summary_json": "data/dashboard.json",
    }
    sanitize_recommendations(dashboard.get("recommendations", []))
    sanitize_recommendations(dashboard.get("core_recommendations", []))
    sanitize_data_files(dashboard.get("data_files", {}))
    return dashboard


def sanitize_recommendations(value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        item["user_status"] = ""
        item["user_note"] = ""
        item["paper_position"] = {}


def sanitize_data_files(value: object) -> None:
    if not isinstance(value, dict):
        return
    for item in value.values():
        if isinstance(item, dict):
            item["path"] = ""


def write_ticker_payloads(program_root: Path, output_dir: Path, dashboard: dict[str, object]) -> int:
    recommendations = dashboard_recommendations_by_ticker(dashboard)
    histories = load_histories_by_ticker(program_root, set(recommendations))
    count = 0
    for ticker, recommendation in recommendations.items():
        history = histories.get(ticker, [])
        payload = {
            "ticker": ticker,
            "recommendation": recommendation,
            "decision": {"ticker": ticker, "status": "", "note": "", "updated_at": ""},
            "open_position": {},
            "history": history,
            "history_summary": summarize_ticker_history(history),
        }
        path = output_dir / "data" / "tickers" / f"{ticker}.json"
        path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        count += 1
    return count


def dashboard_recommendations_by_ticker(dashboard: dict[str, object]) -> dict[str, dict[str, object]]:
    recommendations: dict[str, dict[str, object]] = {}
    for key in ["recommendations", "core_recommendations"]:
        rows = dashboard.get(key, [])
        if not isinstance(rows, list):
            continue
        for item in rows:
            if isinstance(item, dict) and item.get("ticker"):
                ticker = str(item.get("ticker")).zfill(6)
                if len(ticker) == 6:
                    recommendations.setdefault(ticker, item)
    return dict(sorted(recommendations.items()))


def load_histories_by_ticker(program_root: Path, tickers: set[str], limit: int = 160) -> dict[str, list[dict[str, object]]]:
    if not tickers:
        return {}
    path = program_root / "data" / "kr_stock_price_history.csv"
    if not path.exists():
        return {ticker: [] for ticker in tickers}
    columns = {"ticker", "company", "market", "source_bas_dt", "open", "high", "low", "close", "volume", "trading_value"}
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str}, usecols=lambda col: col in columns, low_memory=False)
    except Exception:
        return {ticker: [] for ticker in tickers}
    if frame.empty or "ticker" not in frame.columns:
        return {ticker: [] for ticker in tickers}
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame = frame[frame["ticker"].isin(tickers)].copy()
    if frame.empty:
        return {ticker: [] for ticker in tickers}
    frame = frame.sort_values(["ticker", "source_bas_dt"])
    for column in ["open", "high", "low", "close", "volume", "trading_value"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "close" in frame.columns:
        frame["ma20"] = frame.groupby("ticker")["close"].transform(lambda series: series.rolling(20, min_periods=1).mean())
    histories: dict[str, list[dict[str, object]]] = {}
    for ticker, group in frame.groupby("ticker", sort=True):
        histories[str(ticker)] = json_ready(group.tail(limit).to_dict(orient="records"))
    return {ticker: histories.get(ticker, []) for ticker in tickers}


def copy_reports(program_root: Path, output_dir: Path) -> None:
    report_sources = {
        "latest.html": program_root / "output" / "investment_recommender" / "latest.html",
        "core.html": program_root / "output" / "fundamental_core" / "latest.html",
    }
    for name, source in report_sources.items():
        if source.exists():
            shutil.copy2(source, output_dir / "reports" / name)


if __name__ == "__main__":
    raise SystemExit(main())
