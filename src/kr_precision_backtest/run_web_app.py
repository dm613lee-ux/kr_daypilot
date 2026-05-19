from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd

from .investment_recommender import json_ready


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROGRAM_ROOT / "webapp"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
APP_BUILD_ID = "20260519-fundamental-core-v1"
APP_STATE_DIR = Path("runtime") / "webapp"
DECISIONS_FILE = "user_decisions.json"
PAPER_LEDGER_FILE = "paper_ledger.csv"
DECISION_STATUSES = {"watch", "exclude", "memo", "clear", ""}
LEDGER_COLUMNS = [
    "entry_id",
    "opened_at",
    "entry_date",
    "ticker",
    "company",
    "technique",
    "final_score",
    "entry_price",
    "quantity",
    "status",
    "note",
    "close_date",
    "close_price",
]


@dataclass
class JobState:
    running: bool = False
    started_at: str = ""
    finished_at: str = ""
    returncode: int | None = None
    command: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "running": self.running,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "returncode": self.returncode,
                "command": self.command,
                "lines": self.lines[-120:],
            }


JOB_STATE = JobState()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the KR DayPilot local web app.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    handler = partial(KrDayPilotHandler, directory=str(WEB_ROOT), program_root=PROGRAM_ROOT)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"KR DayPilot web app running: {url}")
    if args.open:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKR DayPilot web app stopped.")
    return 0


class KrDayPilotHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, program_root: Path, **kwargs: Any) -> None:
        self.program_root = program_root
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/health":
            self.send_json(health_payload(self.program_root))
            return
        if path == "/api/dashboard":
            self.send_json(load_dashboard_payload(self.program_root))
            return
        if path == "/api/ticker":
            ticker = first_query_value(query, "ticker")
            try:
                self.send_json(load_ticker_detail_payload(self.program_root, ticker))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/job":
            self.send_json(JOB_STATE.snapshot())
            return
        if path == "/report/latest.html":
            self.serve_fixed_file(self.program_root / "output" / "investment_recommender" / "latest.html", "text/html; charset=utf-8")
            return
        if path == "/report/core.html":
            self.serve_fixed_file(self.program_root / "output" / "fundamental_core" / "latest.html", "text/html; charset=utf-8")
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/api/pipeline/start":
            options = sanitize_pipeline_options(self.read_json_body())
            try:
                snapshot = start_pipeline_job(options, cwd=self.program_root)
            except RuntimeError as exc:
                self.send_json({"error": str(exc), "job": JOB_STATE.snapshot()}, status=HTTPStatus.CONFLICT)
                return
            self.send_json(snapshot, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/decision/save":
            try:
                self.send_json(save_user_decision(self.program_root, self.read_json_body()))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/ledger/add":
            try:
                self.send_json(add_paper_position(self.program_root, self.read_json_body()), status=HTTPStatus.CREATED)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/ledger/close":
            try:
                self.send_json(close_paper_position(self.program_root, self.read_json_body()))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/ledger/remove":
            try:
                self.send_json(remove_paper_position(self.program_root, self.read_json_body()))
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown API route.")

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 20_000))
        try:
            body = json.loads(raw.decode("utf-8"))
            return body if isinstance(body, dict) else {}
        except json.JSONDecodeError:
            return {}

    def send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(json_ready(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        if not urlparse(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def serve_fixed_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found.")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def load_dashboard_payload(program_root: Path = PROGRAM_ROOT) -> dict[str, object]:
    recommendation_path = program_root / "output" / "investment_recommender" / "latest_summary.json"
    core_recommendation_path = program_root / "output" / "fundamental_core" / "latest_summary.json"
    pipeline_path = program_root / "output" / "investment_recommender_pipeline" / "latest_summary.json"
    recommendation_payload = read_json_file(recommendation_path)
    core_recommendation_payload = read_json_file(core_recommendation_path)
    pipeline_payload = read_json_file(pipeline_path)
    recommendations = normalize_recommendations(recommendation_payload.get("recommendations", []))
    core_recommendations = normalize_recommendations(core_recommendation_payload.get("recommendations", []))
    mark_engine(recommendations, "tactical")
    mark_engine(core_recommendations, "core")
    user_decisions = load_user_decisions(program_root)
    paper_ledger = load_paper_ledger(program_root)
    recommendations = merge_user_state(recommendations, user_decisions, paper_ledger)
    core_recommendations = merge_user_state(core_recommendations, user_decisions, paper_ledger)
    summary = recommendation_payload.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    core_summary = core_recommendation_payload.get("summary", {})
    if not isinstance(core_summary, dict):
        core_summary = {}
    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "summary": summary,
        "core_summary": core_summary,
        "config": recommendation_payload.get("config", {}),
        "core_config": core_recommendation_payload.get("config", {}),
        "inputs": recommendation_payload.get("inputs", {}),
        "recommendations": recommendations,
        "core_recommendations": core_recommendations,
        "technique_breakdown": technique_breakdown(recommendations),
        "core_technique_breakdown": technique_breakdown(core_recommendations),
        "engine_comparison": build_engine_comparison(recommendations, core_recommendations),
        "user_decisions": user_decisions,
        "paper_ledger": paper_ledger,
        "paper_portfolio_summary": summarize_paper_ledger(paper_ledger),
        "pipeline": pipeline_payload,
        "job": JOB_STATE.snapshot(),
        "data_files": data_file_status(program_root),
        "files": {
            "latest_report_html": "/report/latest.html" if (program_root / "output" / "investment_recommender" / "latest.html").exists() else "",
            "latest_summary_json": str(recommendation_path),
            "core_report_html": "/report/core.html" if (program_root / "output" / "fundamental_core" / "latest.html").exists() else "",
            "core_summary_json": str(core_recommendation_path),
        },
    }


def health_payload(program_root: Path = PROGRAM_ROOT) -> dict[str, object]:
    summary_path = program_root / "output" / "investment_recommender" / "latest_summary.json"
    price_path = program_root / "data" / "kr_stock_price_history.csv"
    return {
        "ok": True,
        "app_build_id": APP_BUILD_ID,
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "summary_exists": summary_path.exists(),
        "price_history_exists": price_path.exists(),
        "job_running": JOB_STATE.snapshot()["running"],
    }


def first_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return values[0] if values else ""


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}


def normalize_recommendations(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    recommendations: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["ticker"] = normalize_ticker(normalized.get("ticker"))
        recommendations.append(normalized)
    return recommendations


def mark_engine(recommendations: list[dict[str, object]], engine: str) -> None:
    for item in recommendations:
        item["engine"] = engine


def build_engine_comparison(
    tactical_recommendations: list[dict[str, object]],
    core_recommendations: list[dict[str, object]],
) -> dict[str, object]:
    tactical_by_ticker = {str(item.get("ticker") or ""): item for item in tactical_recommendations if item.get("ticker")}
    core_by_ticker = {str(item.get("ticker") or ""): item for item in core_recommendations if item.get("ticker")}
    rows: list[dict[str, object]] = []
    counts = {"consensus": 0, "core_pick": 0, "tactical_watch": 0}
    for ticker in sorted(set(tactical_by_ticker) | set(core_by_ticker)):
        tactical = tactical_by_ticker.get(ticker, {})
        core = core_by_ticker.get(ticker, {})
        if tactical and core:
            category = "consensus"
        elif core:
            category = "core_pick"
        else:
            category = "tactical_watch"
        counts[category] += 1
        company = str(core.get("company") or tactical.get("company") or "")
        market = str(core.get("market") or tactical.get("market") or "")
        tactical_score = safe_float(tactical.get("final_score"))
        core_score = safe_float(core.get("final_score"))
        available_scores = [score for score in [tactical_score, core_score] if score is not None]
        combined_score = sum(available_scores) / len(available_scores) if available_scores else None
        rows.append(
            {
                "ticker": ticker,
                "company": company,
                "market": market,
                "category": category,
                "combined_score": combined_score,
                "tactical_score": tactical_score,
                "core_score": core_score,
                "tactical_state": tactical.get("state", ""),
                "core_state": core.get("state", ""),
                "tactical_technique": tactical.get("technique", ""),
                "core_technique": core.get("technique", ""),
            }
        )
    category_rank = {"consensus": 0, "core_pick": 1, "tactical_watch": 2}
    rows.sort(key=lambda row: (category_rank.get(str(row.get("category")), 9), -(safe_float(row.get("combined_score")) or -1), str(row.get("ticker"))))
    return {"counts": counts, "rows": rows}


def merge_user_state(
    recommendations: list[dict[str, object]],
    decisions: dict[str, dict[str, object]],
    ledger: list[dict[str, object]],
) -> list[dict[str, object]]:
    open_positions = {
        str(item.get("ticker")): item
        for item in ledger
        if str(item.get("status") or "").lower() == "open"
    }
    enriched: list[dict[str, object]] = []
    for item in recommendations:
        ticker = str(item.get("ticker") or "")
        decision = decisions.get(ticker, {})
        row = dict(item)
        row["user_status"] = decision.get("status", "")
        row["user_note"] = decision.get("note", "")
        row["paper_position"] = open_positions.get(ticker, {})
        enriched.append(row)
    return enriched


def app_state_dir(program_root: Path) -> Path:
    return program_root / APP_STATE_DIR


def decisions_path(program_root: Path) -> Path:
    return app_state_dir(program_root) / DECISIONS_FILE


def ledger_path(program_root: Path) -> Path:
    return app_state_dir(program_root) / PAPER_LEDGER_FILE


def load_user_decisions(program_root: Path = PROGRAM_ROOT) -> dict[str, dict[str, object]]:
    path = decisions_path(program_root)
    payload = read_json_file(path)
    raw = payload.get("decisions", {})
    if not isinstance(raw, dict):
        return {}
    decisions: dict[str, dict[str, object]] = {}
    for ticker, item in raw.items():
        if not isinstance(item, dict):
            continue
        normalized_ticker = normalize_ticker(ticker)
        if not normalized_ticker:
            continue
        decisions[normalized_ticker] = {
            "ticker": normalized_ticker,
            "status": normalize_decision_status(item.get("status")),
            "note": sanitize_note(item.get("note")),
            "updated_at": str(item.get("updated_at") or ""),
        }
    return decisions


def save_user_decision(program_root: Path, body: dict[str, object]) -> dict[str, object]:
    ticker = normalize_ticker(body.get("ticker"))
    if not ticker:
        raise ValueError("ticker_required")
    note = sanitize_note(body.get("note"))
    status = normalize_decision_status(body.get("status"))
    if not status and note:
        status = "memo"
    decisions = load_user_decisions(program_root)
    if status == "clear" or (not status and not note):
        decisions.pop(ticker, None)
        decision: dict[str, object] = {"ticker": ticker, "status": "", "note": "", "updated_at": ""}
    else:
        decision = {
            "ticker": ticker,
            "status": status or "memo",
            "note": note,
            "updated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        }
        decisions[ticker] = decision
    write_user_decisions(program_root, decisions)
    return {"decision": decision, "decisions": decisions}


def write_user_decisions(program_root: Path, decisions: dict[str, dict[str, object]]) -> None:
    path = decisions_path(program_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"decisions": decisions}, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_decision_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status not in DECISION_STATUSES:
        return ""
    return status


def sanitize_note(value: object) -> str:
    return str(value or "").strip()[:500]


def normalize_ticker(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    return digits[-6:].zfill(6)


def technique_breakdown(recommendations: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in recommendations:
        if not isinstance(item, dict):
            continue
        technique = str(item.get("technique", "") or "Unclassified")
        counts[technique] = counts.get(technique, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def data_file_status(program_root: Path) -> dict[str, dict[str, object]]:
    paths = {
        "price_history": program_root / "data" / "kr_stock_price_history.csv",
        "investor_flows": program_root / "data" / "eod_context" / "investor_flows.csv",
        "disclosures": program_root / "data" / "eod_context" / "disclosures.csv",
        "fundamentals": program_root / "data" / "fundamentals" / "fundamental_snapshots.csv",
        "valuation": program_root / "data" / "fundamentals" / "krx_valuation.csv",
    }
    return {name: file_status(path) for name, path in paths.items()}


def file_status(path: Path) -> dict[str, object]:
    exists = path.exists()
    rows = 0
    latest_day = ""
    if exists:
        try:
            frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str}, usecols=lambda col: col in {"source_bas_dt", "ticker"}, low_memory=False)
            rows = int(len(frame))
            if "source_bas_dt" in frame.columns and not frame.empty:
                latest_day = str(frame["source_bas_dt"].astype(str).str[:8].max())
        except Exception:
            rows = 0
    return {
        "exists": exists,
        "rows": rows,
        "latest_day": latest_day,
        "path": str(path),
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=KST).isoformat(timespec="seconds") if exists else "",
    }


def load_latest_recommendation_payload(program_root: Path) -> dict[str, object]:
    return read_json_file(program_root / "output" / "investment_recommender" / "latest_summary.json")


def load_latest_core_payload(program_root: Path) -> dict[str, object]:
    return read_json_file(program_root / "output" / "fundamental_core" / "latest_summary.json")


def find_recommendation(program_root: Path, ticker: str) -> dict[str, object]:
    for payload in [load_latest_recommendation_payload(program_root), load_latest_core_payload(program_root)]:
        for item in normalize_recommendations(payload.get("recommendations", [])):
            if item.get("ticker") == ticker:
                return item
    return {}


def load_ticker_detail_payload(program_root: Path, ticker_value: object) -> dict[str, object]:
    ticker = normalize_ticker(ticker_value)
    if not ticker:
        raise ValueError("ticker_required")
    recommendation = find_recommendation(program_root, ticker)
    decisions = load_user_decisions(program_root)
    ledger = load_paper_ledger(program_root)
    open_position = next((item for item in ledger if item.get("ticker") == ticker and item.get("status") == "open"), {})
    history = load_ticker_history(program_root, ticker)
    return {
        "ticker": ticker,
        "recommendation": recommendation,
        "decision": decisions.get(ticker, {"ticker": ticker, "status": "", "note": "", "updated_at": ""}),
        "open_position": open_position,
        "history": history,
        "history_summary": summarize_ticker_history(history),
    }


def load_ticker_history(program_root: Path, ticker: str, limit: int = 160) -> list[dict[str, object]]:
    path = program_root / "data" / "kr_stock_price_history.csv"
    if not path.exists():
        return []
    columns = {"ticker", "company", "market", "source_bas_dt", "open", "high", "low", "close", "volume", "trading_value"}
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str}, usecols=lambda col: col in columns, low_memory=False)
    except Exception:
        return []
    if frame.empty or "ticker" not in frame.columns:
        return []
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame = frame[frame["ticker"] == ticker].copy()
    if frame.empty:
        return []
    frame = frame.sort_values("source_bas_dt").tail(limit)
    for column in ["open", "high", "low", "close", "volume", "trading_value"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "close" in frame.columns:
        frame["ma20"] = frame["close"].rolling(20, min_periods=1).mean()
    records: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        records.append({key: json_ready_value(value) for key, value in row.items()})
    return records


def summarize_ticker_history(history: list[dict[str, object]]) -> dict[str, object]:
    closes = [safe_float(row.get("close")) for row in history if safe_float(row.get("close")) is not None]
    if not closes:
        return {"points": 0, "latest_close": None, "return_pct": None}
    first = closes[0]
    latest = closes[-1]
    return {
        "points": len(closes),
        "latest_close": latest,
        "min_close": min(closes),
        "max_close": max(closes),
        "return_pct": ((latest / first) - 1.0) * 100 if first else None,
    }


def load_latest_prices(program_root: Path) -> dict[str, dict[str, object]]:
    path = program_root / "data" / "kr_stock_price_history.csv"
    if not path.exists():
        return {}
    columns = {"ticker", "source_bas_dt", "close"}
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str}, usecols=lambda col: col in columns, low_memory=False)
    except Exception:
        return {}
    if frame.empty:
        return {}
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["close"]).sort_values(["ticker", "source_bas_dt"])
    latest = frame.groupby("ticker", as_index=False).tail(1)
    return {
        str(row["ticker"]): {"latest_close": float(row["close"]), "latest_day": str(row["source_bas_dt"])}
        for row in latest.to_dict(orient="records")
    }


def load_paper_ledger(program_root: Path = PROGRAM_ROOT) -> list[dict[str, object]]:
    path = ledger_path(program_root)
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "entry_date": str, "close_date": str, "entry_id": str}, low_memory=False)
    except Exception:
        return []
    if frame.empty:
        return []
    for column in LEDGER_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    latest_prices = load_latest_prices(program_root)
    ledger: list[dict[str, object]] = []
    for row in frame[LEDGER_COLUMNS].fillna("").to_dict(orient="records"):
        item = normalize_ledger_row(row, latest_prices)
        ledger.append(item)
    return ledger


def normalize_ledger_row(row: dict[str, object], latest_prices: dict[str, dict[str, object]]) -> dict[str, object]:
    ticker = normalize_ticker(row.get("ticker"))
    entry_price = safe_float(row.get("entry_price")) or 0.0
    quantity = safe_int(row.get("quantity"), default=1)
    status = str(row.get("status") or "open").lower()
    latest = latest_prices.get(ticker, {})
    close_price = safe_float(row.get("close_price"))
    current_price = close_price if status == "closed" and close_price is not None else safe_float(latest.get("latest_close"))
    latest_day = str(row.get("close_date") or latest.get("latest_day") or "")
    pnl_pct = None
    pnl_krw = None
    if current_price is not None and entry_price > 0:
        pnl_pct = ((current_price / entry_price) - 1.0) * 100
        pnl_krw = (current_price - entry_price) * quantity
    return {
        "entry_id": str(row.get("entry_id") or ""),
        "opened_at": str(row.get("opened_at") or ""),
        "entry_date": str(row.get("entry_date") or ""),
        "ticker": ticker,
        "company": str(row.get("company") or ""),
        "technique": str(row.get("technique") or ""),
        "final_score": safe_float(row.get("final_score")),
        "entry_price": entry_price,
        "quantity": quantity,
        "status": status,
        "note": str(row.get("note") or ""),
        "close_date": str(row.get("close_date") or ""),
        "close_price": close_price,
        "latest_close": current_price,
        "latest_day": latest_day,
        "pnl_pct": pnl_pct,
        "pnl_krw": pnl_krw,
        "holding_days": holding_days(str(row.get("entry_date") or ""), latest_day),
    }


def add_paper_position(program_root: Path, body: dict[str, object]) -> dict[str, object]:
    ticker = normalize_ticker(body.get("ticker"))
    if not ticker:
        raise ValueError("ticker_required")
    recommendation = find_recommendation(program_root, ticker)
    if not recommendation:
        raise ValueError("recommendation_not_found")
    ledger = load_paper_ledger(program_root)
    existing = next((item for item in ledger if item.get("ticker") == ticker and item.get("status") == "open"), None)
    if existing:
        return {"created": False, "entry": existing, "paper_ledger": ledger, "paper_portfolio_summary": summarize_paper_ledger(ledger)}
    entry_price = safe_float(body.get("entry_price")) or safe_float(recommendation.get("close"))
    if not entry_price or entry_price <= 0:
        latest = load_latest_prices(program_root).get(ticker, {})
        entry_price = safe_float(latest.get("latest_close"))
    if not entry_price or entry_price <= 0:
        raise ValueError("entry_price_required")
    quantity = max(1, min(safe_int(body.get("quantity"), default=1), 1_000_000))
    opened_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    entry_date = str(body.get("entry_date") or recommendation.get("source_bas_dt") or "")
    entry = {
        "entry_id": f"{ticker}-{datetime.now(tz=KST).strftime('%Y%m%d%H%M%S')}",
        "opened_at": opened_at,
        "entry_date": entry_date,
        "ticker": ticker,
        "company": str(recommendation.get("company") or ""),
        "technique": str(recommendation.get("technique") or ""),
        "final_score": safe_float(recommendation.get("final_score")),
        "entry_price": entry_price,
        "quantity": quantity,
        "status": "open",
        "note": sanitize_note(body.get("note")),
        "close_date": "",
        "close_price": "",
    }
    rows = read_ledger_rows(program_root)
    rows.append(entry)
    write_ledger_rows(program_root, rows)
    ledger = load_paper_ledger(program_root)
    created_entry = next((item for item in ledger if item.get("entry_id") == entry["entry_id"]), entry)
    return {"created": True, "entry": created_entry, "paper_ledger": ledger, "paper_portfolio_summary": summarize_paper_ledger(ledger)}


def close_paper_position(program_root: Path, body: dict[str, object]) -> dict[str, object]:
    ticker = normalize_ticker(body.get("ticker"))
    entry_id = str(body.get("entry_id") or "").strip()
    if not ticker and not entry_id:
        raise ValueError("ticker_or_entry_id_required")
    rows = read_ledger_rows(program_root)
    if not rows:
        raise ValueError("position_not_found")
    latest_prices = load_latest_prices(program_root)
    updated = False
    close_date = str(body.get("close_date") or "")
    for row in rows:
        same_entry = entry_id and str(row.get("entry_id") or "") == entry_id
        same_ticker = ticker and normalize_ticker(row.get("ticker")) == ticker and str(row.get("status") or "open").lower() == "open"
        if same_entry or same_ticker:
            latest = latest_prices.get(normalize_ticker(row.get("ticker")), {})
            row["status"] = "closed"
            row["close_date"] = close_date or str(latest.get("latest_day") or "")
            row["close_price"] = safe_float(body.get("close_price")) or safe_float(latest.get("latest_close")) or safe_float(row.get("entry_price")) or 0.0
            updated = True
            break
    if not updated:
        raise ValueError("position_not_found")
    write_ledger_rows(program_root, rows)
    ledger = load_paper_ledger(program_root)
    return {"closed": True, "paper_ledger": ledger, "paper_portfolio_summary": summarize_paper_ledger(ledger)}


def remove_paper_position(program_root: Path, body: dict[str, object]) -> dict[str, object]:
    ticker = normalize_ticker(body.get("ticker"))
    entry_id = str(body.get("entry_id") or "").strip()
    if not ticker and not entry_id:
        raise ValueError("ticker_or_entry_id_required")
    rows = read_ledger_rows(program_root)
    kept_rows: list[dict[str, object]] = []
    removed_row: dict[str, object] | None = None
    for row in rows:
        same_entry = entry_id and str(row.get("entry_id") or "") == entry_id
        same_ticker = ticker and normalize_ticker(row.get("ticker")) == ticker and str(row.get("status") or "open").lower() == "open"
        if removed_row is None and (same_entry or same_ticker):
            removed_row = row
            continue
        kept_rows.append(row)
    if removed_row is None:
        raise ValueError("position_not_found")
    write_ledger_rows(program_root, kept_rows)
    ledger = load_paper_ledger(program_root)
    return {
        "removed": True,
        "removed_entry": normalize_ledger_row(removed_row, load_latest_prices(program_root)),
        "paper_ledger": ledger,
        "paper_portfolio_summary": summarize_paper_ledger(ledger),
    }


def read_ledger_rows(program_root: Path) -> list[dict[str, object]]:
    path = ledger_path(program_root)
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "entry_date": str, "close_date": str, "entry_id": str}, low_memory=False)
    except Exception:
        return []
    for column in LEDGER_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[LEDGER_COLUMNS].fillna("").to_dict(orient="records")


def write_ledger_rows(program_root: Path, rows: list[dict[str, object]]) -> None:
    path = ledger_path(program_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    frame.to_csv(path, index=False, encoding="utf-8")


def summarize_paper_ledger(ledger: list[dict[str, object]]) -> dict[str, object]:
    open_items = [item for item in ledger if item.get("status") == "open"]
    total_cost = sum((safe_float(item.get("entry_price")) or 0.0) * safe_int(item.get("quantity"), default=0) for item in open_items)
    total_pnl = sum(safe_float(item.get("pnl_krw")) or 0.0 for item in open_items)
    pnl_pcts = [safe_float(item.get("pnl_pct")) for item in open_items if safe_float(item.get("pnl_pct")) is not None]
    return {
        "positions": len(ledger),
        "open_positions": len(open_items),
        "closed_positions": len(ledger) - len(open_items),
        "total_cost": total_cost,
        "total_pnl_krw": total_pnl,
        "avg_pnl_pct": (sum(pnl_pcts) / len(pnl_pcts)) if pnl_pcts else None,
    }


def holding_days(entry_date: str, latest_day: str) -> int | None:
    try:
        start = datetime.strptime(entry_date[:8], "%Y%m%d")
        end = datetime.strptime(latest_day[:8], "%Y%m%d")
    except (ValueError, TypeError):
        return None
    return max(0, (end - start).days)


def safe_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def safe_int(value: object, *, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def json_ready_value(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def sanitize_pipeline_options(body: dict[str, object]) -> dict[str, object]:
    return {
        "price_max_tickers": clamp_int(body.get("price_max_tickers"), default=200, min_value=1, max_value=1000),
        "eod_max_tickers": clamp_int(body.get("eod_max_tickers"), default=30, min_value=0, max_value=500),
        "fundamental_max_tickers": clamp_int(body.get("fundamental_max_tickers"), default=30, min_value=0, max_value=500),
        "top": clamp_int(body.get("top"), default=15, min_value=1, max_value=50),
        "allow_stale_data": bool(body.get("allow_stale_data", False)),
    }


def clamp_int(value: object, *, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, min_value), max_value)


def build_pipeline_command(options: dict[str, object], *, python_executable: str = sys.executable) -> list[str]:
    command = [
        python_executable,
        "-m",
        "kr_precision_backtest.run_recommender_pipeline",
        "--price-source",
        "auto",
        "--price-max-tickers",
        str(options["price_max_tickers"]),
        "--eod-max-tickers",
        str(options["eod_max_tickers"]),
        "--fundamental-max-tickers",
        str(options["fundamental_max_tickers"]),
        "--top",
        str(options["top"]),
    ]
    if options.get("allow_stale_data"):
        command.append("--allow-stale-data")
    return command


def start_pipeline_job(options: dict[str, object], *, cwd: Path) -> dict[str, object]:
    with JOB_STATE.lock:
        if JOB_STATE.running:
            raise RuntimeError("pipeline_already_running")
        JOB_STATE.running = True
        JOB_STATE.started_at = datetime.now(tz=KST).isoformat(timespec="seconds")
        JOB_STATE.finished_at = ""
        JOB_STATE.returncode = None
        JOB_STATE.lines = []
        JOB_STATE.command = build_pipeline_command(options)
    thread = threading.Thread(target=run_job_thread, args=(cwd,), daemon=True)
    thread.start()
    return JOB_STATE.snapshot()


def run_job_thread(cwd: Path) -> None:
    command = JOB_STATE.snapshot()["command"]
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            append_job_line(line.rstrip())
        returncode = process.wait()
    except Exception as exc:
        append_job_line(f"webapp job error: {exc}")
        returncode = 1
    with JOB_STATE.lock:
        JOB_STATE.running = False
        JOB_STATE.returncode = int(returncode)
        JOB_STATE.finished_at = datetime.now(tz=KST).isoformat(timespec="seconds")


def append_job_line(line: str) -> None:
    with JOB_STATE.lock:
        JOB_STATE.lines.append(line)
        if len(JOB_STATE.lines) > 500:
            JOB_STATE.lines = JOB_STATE.lines[-500:]


if __name__ == "__main__":
    raise SystemExit(main())
