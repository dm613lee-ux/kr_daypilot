from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import html
import io
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd

from .env_config import load_env_file, load_kis_credentials
from .kis_client import KisApiError, KisClient


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = PROGRAM_ROOT / ".env"
DEFAULT_TOKEN_CACHE = PROGRAM_ROOT / "runtime" / "kis_token.json"
DEFAULT_LATEST_RESULTS = PROGRAM_ROOT / "output" / "latest.csv"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_OUTPUT_ROOT = PROGRAM_ROOT / "data" / "live_context"
DEFAULT_REPORT_OUTPUT = PROGRAM_ROOT / "output" / "risk_context"
DEFAULT_REFERENCE_DIR = PROGRAM_ROOT / "data" / "reference"

INDEX_CODES = {
    "KOSPI": "0001",
    "KOSDAQ": "1001",
}
MARKET_CLASS_CODES = {
    "KOSPI": "K",
    "KOSDAQ": "Q",
}

RISK_KEYWORDS = [
    "\uc720\uc0c1\uc99d\uc790",
    "\uc804\ud658\uc0ac\ucc44",
    "CB",
    "BW",
    "\ud6a1\ub839",
    "\ubc30\uc784",
    "\uac70\ub798\uc815\uc9c0",
    "\ud22c\uc790\uc8fc\uc758",
    "\ud22c\uc790\uacbd\uace0",
    "\ud22c\uc790\uc704\ud5d8",
    "\ubd88\uc131\uc2e4\uacf5\uc2dc",
    "\uac10\uc0ac\uc758\uacac",
    "\uac10\uc0ac\uc758\uacac\uac70\uc808",
    "\uc18c\uc1a1",
    "\uc601\uc5c5\uc815\uc9c0",
    "\uc2e4\uc801\uc545\ud654",
    "\uacc4\uc57d\ud574\uc9c0",
    "\ud558\ud55c\uac00",
    "\uad00\ub9ac\uc885\ubaa9",
    "\uc0c1\uc7a5\ud3d0\uc9c0",
]


class ExternalApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    ticker: str
    company: str
    market: str
    rank: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect observe-only live risk context for KR DayPilot candidates.")
    parser.add_argument("--tickers", default="", help="Comma separated tickers. Defaults to latest daily candidates.")
    parser.add_argument("--max-tickers", type=int, default=5)
    parser.add_argument("--market-div", default="J", help="J: KRX, NX: NXT, UN: integrated")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--latest-results", type=Path, default=DEFAULT_LATEST_RESULTS)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--dart-days", type=int, default=3)
    parser.add_argument("--news-display", type=int, default=10)
    parser.add_argument("--skip-dart", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    args = parser.parse_args()

    candidates = resolve_candidates(
        args.tickers,
        args.latest_results,
        args.universe,
        max_tickers=max(args.max_tickers, 1),
    )
    if not candidates:
        print("No candidates to collect. Run the daily pipeline first or pass --tickers.")
        return 2

    summaries, paths = collect_risk_context_for_candidates(
        candidates,
        env_file=args.env_file,
        token_cache_path=DEFAULT_TOKEN_CACHE,
        market_div=args.market_div,
        output_root=args.output_root,
        report_output=args.report_output,
        reference_dir=args.reference_dir,
        sleep_seconds=args.sleep_seconds,
        dart_days=args.dart_days,
        news_display=args.news_display,
        skip_dart=args.skip_dart,
        skip_news=args.skip_news,
        echo=True,
    )
    print("Risk context collection complete.")
    print(f"Candidates: {len(candidates)}")
    print(f"CSV: {paths['csv']}")
    print(f"HTML: {paths['html']}")
    return 0


def collect_risk_context_for_candidates(
    candidates: list[Candidate],
    *,
    env_file: Path = DEFAULT_ENV,
    token_cache_path: Path = DEFAULT_TOKEN_CACHE,
    market_div: str = "J",
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    report_output: Path = DEFAULT_REPORT_OUTPUT,
    reference_dir: Path = DEFAULT_REFERENCE_DIR,
    sleep_seconds: float = 1.0,
    dart_days: int = 3,
    news_display: int = 10,
    skip_dart: bool = False,
    skip_news: bool = False,
    echo: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    env = load_env_file(env_file)
    credentials = load_kis_credentials(env_file)
    client = KisClient(credentials=credentials, token_cache_path=token_cache_path)
    dart_client = None if skip_dart else DartClient(env.get("OPENDART_API_KEY", ""), reference_dir)
    news_client = None if skip_news else NaverNewsClient(env.get("NAVER_CLIENT_ID", ""), env.get("NAVER_CLIENT_SECRET", ""))

    index_cache = collect_index_contexts(client, candidates)
    summaries: list[dict[str, Any]] = []
    context_refs: list[dict[str, Any]] = []
    for candidate in candidates:
        context = collect_candidate_context(
            candidate,
            client,
            market_div=market_div,
            index_context=index_cache.get(index_code_for_market(candidate.market), {}),
            dart_client=dart_client,
            dart_days=max(dart_days, 1),
            news_client=news_client,
            news_display=max(min(news_display, 100), 1),
        )
        context_path = write_context_json(context, output_root)
        summary = summarize_context(context)
        summary["context_path"] = str(context_path)
        summaries.append(summary)
        context_refs.append({"ticker": candidate.ticker, "company": candidate.company, "path": str(context_path)})
        if echo:
            print(f"{candidate.ticker}: context saved -> {context_path}")
        time.sleep(max(sleep_seconds, 0.0))

    paths = write_report_outputs(summaries, context_refs, report_output)
    return summaries, paths


def resolve_candidates(tickers_arg: str, latest_results: Path, universe_path: Path, *, max_tickers: int) -> list[Candidate]:
    metadata = load_metadata(latest_results, universe_path)
    if tickers_arg.strip():
        tickers = clean_tickers(tickers_arg.split(","))
        return [candidate_from_metadata(ticker, metadata.get(ticker, {})) for ticker in tickers[:max_tickers]]

    if not latest_results.exists():
        return []
    frame = pd.read_csv(latest_results, dtype=str).fillna("")
    if frame.empty or "ticker" not in frame.columns:
        return []
    if "reference_day" in frame.columns:
        frame = frame[frame["reference_day"].astype(str) == frame["reference_day"].astype(str).max()].copy()
    if "rank" in frame.columns:
        frame["__rank"] = pd.to_numeric(frame["rank"], errors="coerce").fillna(999999)
        frame = frame.sort_values("__rank")

    candidates: list[Candidate] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        ticker = normalize_ticker(row.get("ticker", ""))
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        values = dict(metadata.get(ticker, {}))
        values.update({key: str(row.get(key, "")) for key in row.index})
        candidates.append(candidate_from_metadata(ticker, values))
        if len(candidates) >= max_tickers:
            break
    return candidates


def load_metadata(latest_results: Path, universe_path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in [universe_path, latest_results]:
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype=str).fillna("")
        except (OSError, pd.errors.ParserError):
            continue
        if "ticker" not in frame.columns:
            continue
        for _, row in frame.iterrows():
            ticker = normalize_ticker(row.get("ticker", ""))
            if not ticker:
                continue
            out[ticker] = {key: str(row.get(key, "")) for key in row.index}
    return out


def candidate_from_metadata(ticker: str, values: dict[str, str]) -> Candidate:
    company = first_text(values, ["company", "name", "corp_name"]) or ticker
    market = first_text(values, ["market"]) or ""
    rank = first_text(values, ["rank"]) or ""
    return Candidate(ticker=ticker, company=company, market=market, rank=rank)


def collect_index_contexts(client: KisClient, candidates: list[Candidate]) -> dict[str, dict[str, Any]]:
    codes = sorted({index_code_for_market(candidate.market) for candidate in candidates if index_code_for_market(candidate.market)})
    out: dict[str, dict[str, Any]] = {}
    for code in codes:
        out[code] = {
            "index_code": code,
            "price": safe_call(lambda: client.fetch_index_price(code)),
            "minutes": safe_call(lambda: client.fetch_index_minutes(code, input_hour="30")),
            "category": safe_call(lambda code=code: client.fetch_index_category_price(code, market_cls_code=market_class_for_index(code))),
        }
        time.sleep(0.4)
    return out


def collect_candidate_context(
    candidate: Candidate,
    client: KisClient,
    *,
    market_div: str,
    index_context: dict[str, Any],
    dart_client: "DartClient | None",
    dart_days: int,
    news_client: "NaverNewsClient | None",
    news_display: int,
) -> dict[str, Any]:
    now = datetime.now(tz=KST)
    ticker = candidate.ticker
    context: dict[str, Any] = {
        "schema_version": "r1.observe_only.1",
        "collection_mode": "observe_only",
        "collected_at": now.isoformat(),
        "candidate": asdict(candidate),
        "kis": {
            "current_price": safe_call(lambda: client.fetch_current_price(ticker, market_div=market_div)),
            "orderbook_expected": safe_call(lambda: client.fetch_orderbook_expected(ticker, market_div=market_div)),
            "current_conclusion": safe_call(lambda: client.fetch_current_conclusion(ticker, market_div=market_div)),
            "time_item_conclusion": safe_call(
                lambda: client.fetch_time_item_conclusion(ticker, market_div=market_div, input_hour=now.strftime("%H%M%S"))
            ),
            "index": index_context,
        },
        "external": {},
        "risk_flags": [],
    }
    if dart_client is None:
        context["external"]["dart"] = {"status": "skipped", "reason": "missing_or_disabled"}
    else:
        context["external"]["dart"] = safe_call(lambda: dart_client.search_disclosures(ticker, days=dart_days))

    if news_client is None:
        context["external"]["news"] = {"status": "skipped", "reason": "missing_or_disabled"}
    else:
        query = candidate.company if candidate.company != ticker else ticker
        context["external"]["news"] = safe_call(lambda: news_client.search(query, display=news_display))

    context["risk_flags"] = build_risk_flags(context)
    return context


def safe_call(fn: Any) -> dict[str, Any]:
    try:
        return {"status": "ok", "data": fn()}
    except (
        KisApiError,
        ExternalApiError,
        HTTPError,
        URLError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        ET.ParseError,
    ) as exc:
        return {"status": "error", "error": short_error(exc), "data": None}


class DartClient:
    def __init__(self, api_key: str, reference_dir: Path) -> None:
        self.api_key = api_key.strip()
        self.reference_dir = reference_dir

    def search_disclosures(self, ticker: str, *, days: int) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "skipped", "reason": "missing_key", "items": [], "risk_keywords": []}
        corp_code = self.corp_code_for_ticker(ticker)
        if not corp_code:
            return {"status": "missing_corp_code", "items": [], "risk_keywords": []}
        end = datetime.now(tz=KST).date()
        begin = end - timedelta(days=max(days, 1))
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": begin.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "sort": "date",
            "sort_mth": "desc",
            "page_no": "1",
            "page_count": "100",
        }
        body = request_json("https://opendart.fss.or.kr/api/list.json", params=params)
        status = str(body.get("status") or "")
        if status == "013":
            return {"status": "empty", "items": [], "risk_keywords": []}
        if status and status != "000":
            raise ExternalApiError(str(body.get("message") or status))
        items = [
            {
                "corp_name": str(item.get("corp_name", "")),
                "stock_code": str(item.get("stock_code", "")),
                "report_nm": str(item.get("report_nm", "")),
                "rcept_no": str(item.get("rcept_no", "")),
                "rcept_dt": str(item.get("rcept_dt", "")),
                "rm": str(item.get("rm", "")),
            }
            for item in body.get("list", [])
            if isinstance(item, dict)
        ]
        hits = keyword_hits([item.get("report_nm", "") for item in items])
        return {"status": "ok", "corp_code": corp_code, "items": items, "risk_keywords": hits}

    def corp_code_for_ticker(self, ticker: str) -> str:
        path = self.reference_dir / "dart_corp_codes.csv"
        if not path.exists():
            self.write_corp_code_cache(path)
        try:
            frame = pd.read_csv(path, dtype=str).fillna("")
        except (OSError, pd.errors.ParserError):
            return ""
        matched = frame[frame["stock_code"].astype(str).str.zfill(6) == str(ticker).zfill(6)]
        if matched.empty:
            return ""
        return str(matched.iloc[0]["corp_code"]).zfill(8)

    def write_corp_code_cache(self, path: Path) -> None:
        params = {"crtfc_key": self.api_key}
        url = "https://opendart.fss.or.kr/api/corpCode.xml?" + urlencode(params)
        request = Request(url, headers={"User-Agent": "KR-DayPilot"})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml_name = archive.namelist()[0]
            xml_bytes = archive.read(xml_name)
        root = ET.fromstring(xml_bytes)
        rows: list[dict[str, str]] = []
        for item in root.findall("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            if not stock_code:
                continue
            rows.append(
                {
                    "corp_code": (item.findtext("corp_code") or "").strip(),
                    "corp_name": (item.findtext("corp_name") or "").strip(),
                    "stock_code": stock_code.zfill(6),
                    "modify_date": (item.findtext("modify_date") or "").strip(),
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


class NaverNewsClient:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()

    def search(self, query: str, *, display: int) -> dict[str, Any]:
        if not self.client_id or not self.client_secret:
            return {"status": "skipped", "reason": "missing_key", "items": [], "risk_keywords": []}
        params = {
            "query": query,
            "display": str(display),
            "start": "1",
            "sort": "date",
        }
        url = "https://openapi.naver.com/v1/search/news.json?" + urlencode(params)
        request = Request(
            url,
            headers={
                "X-Naver-Client-Id": self.client_id,
                "X-Naver-Client-Secret": self.client_secret,
                "User-Agent": "KR-DayPilot",
            },
        )
        with urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        items = [
            {
                "title": clean_html(str(item.get("title", ""))),
                "description": clean_html(str(item.get("description", ""))),
                "originallink": str(item.get("originallink", "")),
                "link": str(item.get("link", "")),
                "pubDate": str(item.get("pubDate", "")),
            }
            for item in body.get("items", [])
            if isinstance(item, dict)
        ]
        hits = keyword_hits([f"{item.get('title', '')} {item.get('description', '')}" for item in items])
        return {"status": "ok", "query": query, "items": items, "risk_keywords": hits}


def request_json(url: str, *, params: dict[str, str]) -> dict[str, Any]:
    request = Request(url + "?" + urlencode(params), headers={"User-Agent": "KR-DayPilot"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def summarize_context(context: dict[str, Any]) -> dict[str, Any]:
    candidate = context["candidate"]
    current = data_at(context, "kis", "current_price")
    orderbook = data_at(context, "kis", "orderbook_expected")
    orderbook_summary = summarize_orderbook(orderbook.get("output1", {}) if isinstance(orderbook, dict) else {})
    conclusion_rows = list_data_at(context, "kis", "current_conclusion")
    time_conclusion = data_at(context, "kis", "time_item_conclusion")
    index = context.get("kis", {}).get("index", {})
    index_price = data_at(index, "price")
    index_minutes = data_at(index, "minutes")
    index_category = data_at(index, "category")
    dart = external_payload(context, "dart")
    news = external_payload(context, "news")
    risk_keywords = sorted({str(flag.get("keyword", "")) for flag in context.get("risk_flags", []) if flag.get("keyword")})

    trade_strength = first_number(current, ["tday_rltv", "cttr", "trade_strength"])
    if trade_strength == 0.0 and conclusion_rows:
        trade_strength = first_number(conclusion_rows[0], ["tday_rltv", "cttr", "trade_strength"])

    return {
        "collected_at": context.get("collected_at", ""),
        "ticker": candidate.get("ticker", ""),
        "company": candidate.get("company", ""),
        "market": candidate.get("market", ""),
        "rank": candidate.get("rank", ""),
        "current_price": first_number(current, ["stck_prpr", "prpr", "current_price"]),
        "day_change_pct": first_number(current, ["prdy_ctrt", "rate", "change_rate"]),
        "acc_volume": first_number(current, ["acml_vol", "acc_volume"]),
        "acc_trading_value": first_number(current, ["acml_tr_pbmn", "acc_trading_value"]),
        "best_bid": orderbook_summary["best_bid"],
        "best_ask": orderbook_summary["best_ask"],
        "spread_pct": orderbook_summary["spread_pct"],
        "bid_ask_imbalance_10": orderbook_summary["bid_ask_imbalance_10"],
        "trade_strength": trade_strength,
        "time_conclusion_rows": len(time_conclusion.get("output2", [])) if isinstance(time_conclusion, dict) else 0,
        "index_code": index.get("index_code", ""),
        "index_value": first_number(index_price, ["bstp_nmix_prpr", "stck_prpr", "prpr"]),
        "index_change_pct": first_number(index_price, ["bstp_nmix_prdy_ctrt", "prdy_ctrt", "rate"]),
        "index_minute_rows": len(index_minutes.get("output2", [])) if isinstance(index_minutes, dict) else 0,
        "sector_category_rows": len(index_category.get("output2", [])) if isinstance(index_category, dict) else 0,
        "dart_status": dart.get("status", ""),
        "dart_count": len(dart.get("items", [])),
        "news_status": news.get("status", ""),
        "news_count": len(news.get("items", [])),
        "risk_flag_count": len(context.get("risk_flags", [])),
        "risk_keywords": ",".join(risk_keywords),
    }


def summarize_orderbook(data: dict[str, Any]) -> dict[str, float]:
    best_bid = first_number(data, ["bidp1", "bidp"])
    best_ask = first_number(data, ["askp1", "askp"])
    bid_qty = sum(first_number(data, [f"bidp_rsqn{i}", f"bidp_qty{i}"]) for i in range(1, 11))
    ask_qty = sum(first_number(data, [f"askp_rsqn{i}", f"askp_qty{i}"]) for i in range(1, 11))
    mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
    spread_pct = round((best_ask - best_bid) / mid * 100.0, 4) if mid > 0 else 0.0
    imbalance = round(bid_qty / ask_qty, 4) if ask_qty > 0 else 0.0
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_pct": spread_pct,
        "bid_ask_imbalance_10": imbalance,
    }


def build_risk_flags(context: dict[str, Any]) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    for source in ["dart", "news"]:
        payload = external_payload(context, source)
        for keyword in payload.get("risk_keywords", []):
            flags.append({"source": source, "keyword": str(keyword), "severity": "observe"})
    return flags


def external_payload(context: dict[str, Any], name: str) -> dict[str, Any]:
    value = context.get("external", {}).get(name, {})
    if value.get("status") == "ok" and isinstance(value.get("data"), dict):
        return value["data"]
    return value if isinstance(value, dict) else {}


def data_at(value: dict[str, Any], *path: str) -> dict[str, Any]:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(part, {})
    if isinstance(current, dict) and current.get("status") == "ok" and isinstance(current.get("data"), dict):
        return current["data"]
    return current if isinstance(current, dict) else {}


def list_data_at(value: dict[str, Any], *path: str) -> list[dict[str, Any]]:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return []
        current = current.get(part, {})
    if isinstance(current, dict) and current.get("status") == "ok" and isinstance(current.get("data"), list):
        return [item for item in current["data"] if isinstance(item, dict)]
    return [item for item in current if isinstance(item, dict)] if isinstance(current, list) else []


def write_context_json(context: dict[str, Any], output_root: Path) -> Path:
    date = datetime.now(tz=KST).strftime("%Y%m%d")
    stamp = datetime.now(tz=KST).strftime("%H%M%S")
    ticker = str(context["candidate"]["ticker"]).zfill(6)
    output_dir = output_root / date
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{ticker}_{stamp}.json"
    path.write_text(json.dumps(context, ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def write_report_outputs(rows: list[dict[str, Any]], context_refs: list[dict[str, Any]], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"risk_context_{stamp}.csv"
    json_path = output_dir / f"risk_context_{stamp}.json"
    html_path = output_dir / f"risk_context_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_json = output_dir / "latest.json"
    latest_html = output_dir / "latest.html"
    frame = pd.DataFrame(rows)
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    frame.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    payload = {"generated_at": datetime.now(tz=KST).isoformat(), "rows": rows, "contexts": context_refs}
    json_text = json.dumps(payload, ensure_ascii=True, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    latest_json.write_text(json_text, encoding="utf-8")
    html_text = render_html(frame, payload["generated_at"])
    html_path.write_text(html_text, encoding="utf-8")
    latest_html.write_text(html_text, encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "html": html_path}


def render_html(frame: pd.DataFrame, generated_at: str) -> str:
    table = frame.to_html(index=False, escape=True) if not frame.empty else "<p>No rows.</p>"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Risk Context</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    h1 {{ margin-bottom: 8px; }}
    .note {{ color: #667085; margin-bottom: 20px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: right; }}
    th:nth-child(1), td:nth-child(1), th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot Risk Context</h1>
  <div class="note">Observe-only collection. This report does not block or change recommendations. Generated at {html.escape(generated_at)}</div>
  {table}
</body>
</html>"""


def keyword_hits(texts: list[str]) -> list[str]:
    joined = "\n".join(texts).lower()
    hits = []
    for keyword in RISK_KEYWORDS:
        if keyword.lower() in joined:
            hits.append(keyword)
    return sorted(set(hits))


def first_text(values: dict[str, Any], names: list[str]) -> str:
    for name in names:
        value = str(values.get(name, "")).strip()
        if value:
            return value
    return ""


def first_number(values: dict[str, Any], names: list[str]) -> float:
    for name in names:
        if name in values:
            number = to_number(values.get(name))
            if number != 0.0:
                return number
    return 0.0


def to_number(value: Any) -> float:
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return html.unescape(text).strip()


def clean_tickers(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticker = normalize_ticker(value)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out


def normalize_ticker(value: Any) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ""
    ticker = digits.zfill(6)
    return ticker if len(ticker) == 6 else ""


def index_code_for_market(market: str) -> str:
    return INDEX_CODES.get(str(market).strip().upper(), "")


def market_class_for_index(index_code: str) -> str:
    for market, code in INDEX_CODES.items():
        if code == index_code:
            return MARKET_CLASS_CODES.get(market, "K")
    return "K"


def short_error(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:300] if text else exc.__class__.__name__


if __name__ == "__main__":
    raise SystemExit(main())
