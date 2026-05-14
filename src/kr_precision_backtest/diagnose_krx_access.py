from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime
from html import escape
import io
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from .collect_eod_context import normalize_date
from .env_config import load_env_file


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = PROGRAM_ROOT / ".env"
DEFAULT_SWING_RESULTS = PROGRAM_ROOT / "output" / "swing_backtest" / "latest.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "krx_access"

KRX_OPEN_API_STOCK_URLS = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose KRX Open API and KRX Data Marketplace access for KR DayPilot.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--swing-results", type=Path, default=DEFAULT_SWING_RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ticker", default="")
    parser.add_argument("--date", default="")
    args = parser.parse_args()

    env = load_env_file(args.env_file)
    ticker, reference_day = resolve_probe(args.swing_results, ticker=args.ticker, date=args.date)
    summary = diagnose_krx_access(env, ticker=ticker, reference_day=reference_day)
    paths = write_outputs(summary, args.output)

    print("KR DayPilot KRX access diagnosis complete.")
    print(f"KRX Open API: {summary['krx_open_api']['status']}")
    print(f"KRX Data Marketplace login: {summary['krx_data_marketplace']['status']}")
    print(f"Investor flow probe: {summary['investor_flow_probe']['status']}")
    print(f"Short-sale probe: {summary['short_sale_probe']['status']}")
    print(f"HTML: {paths['html']}")
    return 0


def diagnose_krx_access(env: dict[str, str], *, ticker: str, reference_day: str) -> dict[str, Any]:
    has_krx_api_key = bool(env.get("KRX_API_KEY", "").strip())
    has_krx_login = bool(env.get("KRX_ID", "").strip() and env.get("KRX_PW", "").strip())
    for key in ["KRX_ID", "KRX_PW"]:
        if env.get(key, "").strip() and not os.environ.get(key):
            os.environ[key] = env[key].strip()

    open_api = probe_krx_open_api(env.get("KRX_API_KEY", ""), reference_day) if has_krx_api_key else {
        "status": "missing_key",
        "message": "KRX_API_KEY is missing.",
    }
    if not has_krx_login:
        data_market = {
            "status": "missing_login_env",
            "message": "KRX_ID and KRX_PW are required for pykrx Data Marketplace session.",
        }
        investor = {"status": "skipped", "message": "Missing KRX_ID/KRX_PW."}
        short_sale = {"status": "skipped", "message": "Missing KRX_ID/KRX_PW."}
    else:
        data_market, investor, short_sale = probe_pykrx_data_marketplace(ticker, reference_day)

    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "probe_ticker": ticker,
        "probe_date": reference_day,
        "env_presence": {
            "KRX_API_KEY": has_krx_api_key,
            "KRX_ID": bool(env.get("KRX_ID", "").strip()),
            "KRX_PW": bool(env.get("KRX_PW", "").strip()),
            "OPENDART_API_KEY": bool(env.get("OPENDART_API_KEY", "").strip()),
        },
        "krx_open_api": open_api,
        "krx_data_marketplace": data_market,
        "investor_flow_probe": investor,
        "short_sale_probe": short_sale,
        "next_action": next_action(open_api, data_market, investor, short_sale),
    }


def resolve_probe(path: Path, *, ticker: str, date: str) -> tuple[str, str]:
    probe_ticker = "".join(ch for ch in str(ticker) if ch.isdigit()).zfill(6) if ticker else ""
    probe_date = normalize_date(date)
    if path.exists():
        frame = pd.read_csv(path, dtype={"ticker": str}).fillna("")
        if not probe_ticker and "ticker" in frame.columns and not frame.empty:
            probe_ticker = str(frame.iloc[0]["ticker"]).zfill(6)
        if not probe_date and "reference_day" in frame.columns and not frame.empty:
            probe_date = normalize_date(frame.iloc[0]["reference_day"])
    return probe_ticker or "005930", probe_date or datetime.now(tz=KST).strftime("%Y%m%d")


def probe_krx_open_api(api_key: str, reference_day: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for market, url in KRX_OPEN_API_STOCK_URLS.items():
        try:
            payload = request_krx_open_api(url, api_key=api_key, bas_dd=reference_day)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            results[market] = {"status": "error", "message": short_error(exc)}
            continue
        rows = payload.get("OutBlock_1") or payload.get("outBlock1") or payload.get("output") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            rows = []
        results[market] = {"status": "ok" if rows else "empty", "rows": len(rows)}
    if any(item.get("status") == "ok" for item in results.values()):
        status = "ok"
    elif any(item.get("status") == "error" for item in results.values()):
        status = "error"
    else:
        status = "empty"
    return {"status": status, "markets": results}


def request_krx_open_api(url: str, *, api_key: str, bas_dd: str) -> dict[str, Any]:
    request = Request(
        url + "?" + urlencode({"basDd": bas_dd}),
        headers={"AUTH_KEY": api_key.strip(), "Accept": "application/json", "User-Agent": "KR-DayPilot"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def probe_pykrx_data_marketplace(ticker: str, reference_day: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        capture = io.StringIO()
        with redirect_stdout(capture):
            from pykrx import stock  # type: ignore
    except Exception as exc:
        message = short_error(exc)
        fail = {"status": "import_error", "message": message}
        return fail, {"status": "skipped", "message": message}, {"status": "skipped", "message": message}

    captured = capture.getvalue()
    data_market = {"status": "imported", "message": "pykrx imported without exposing captured login output."}
    if "로그인 실패" in captured or "KRX login" in captured:
        data_market = {"status": "login_failed_or_unavailable", "message": "pykrx could not establish a KRX Data Marketplace session."}
    elif "로그인 완료" in captured:
        data_market = {"status": "login_ok", "message": "pykrx login output indicates success."}

    investor = probe_pykrx_frame(
        lambda: stock.get_market_trading_value_by_date(reference_day, reference_day, ticker),
        expected_columns=["기관합계", "개인", "외국인합계"],
    )
    short_sale = probe_pykrx_frame(
        lambda: stock.get_shorting_value_by_date(reference_day, reference_day, ticker),
        expected_columns=["공매도", "매수", "비중"],
    )
    return data_market, investor, short_sale


def probe_pykrx_frame(fn: Any, *, expected_columns: list[str]) -> dict[str, Any]:
    try:
        with redirect_stdout(io.StringIO()):
            frame = fn()
    except Exception as exc:
        return {"status": "error", "message": short_error(exc)}
    if not isinstance(frame, pd.DataFrame):
        return {"status": "error", "message": "pykrx returned non-dataframe."}
    missing = [col for col in expected_columns if col not in frame.columns]
    if frame.empty:
        return {"status": "empty", "rows": 0, "columns": list(frame.columns), "missing_columns": missing}
    return {"status": "ok" if not missing else "partial", "rows": len(frame), "columns": list(frame.columns), "missing_columns": missing}


def next_action(open_api: dict[str, Any], data_market: dict[str, Any], investor: dict[str, Any], short_sale: dict[str, Any]) -> str:
    if data_market.get("status") == "missing_login_env":
        return "Add KRX_ID and KRX_PW to .env for KRX Data Marketplace / pykrx access, then rerun KRX접근진단_실행.bat."
    if data_market.get("status") != "login_ok" and investor.get("status") != "ok":
        return "KRX Data Marketplace login/session is not usable. Verify KRX account login, password-change requirement, and whether ID/password login is enabled."
    if investor.get("status") == "ok" and short_sale.get("status") == "ok":
        return "Access is ready. Run EOD컨텍스트_수집.bat, then 스윙검증_실행.bat and 피처손절분석_실행.bat."
    if open_api.get("status") == "ok":
        return "KRX Open API works for daily trade data, but investor/short-sale data still needs Data Marketplace or paid Koscom/KRX feed access."
    return "KRX access is not ready. Confirm KRX API approvals and Data Marketplace login credentials."


def write_outputs(summary: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"krx_access_{stamp}.json"
    html_path = output_dir / f"krx_access_{stamp}.html"
    latest_json = output_dir / "latest.json"
    latest_html = output_dir / "latest.html"
    payload = json.dumps({"summary": summary}, ensure_ascii=False, indent=2)
    json_path.write_text(payload, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    html = render_html(summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"json": json_path, "html": html_path, "latest_json": latest_json, "latest_html": latest_html}


def render_html(summary: dict[str, Any]) -> str:
    status = {
        "KRX Open API": summary["krx_open_api"].get("status"),
        "KRX Data Marketplace": summary["krx_data_marketplace"].get("status"),
        "Investor Flow": summary["investor_flow_probe"].get("status"),
        "Short Sale": summary["short_sale_probe"].get("status"),
    }
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot KRX Access Diagnosis</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 8px; }}
    .note {{ color: #667085; line-height: 1.6; }}
    pre {{ background: #f8fafc; border: 1px solid #eaecf0; border-radius: 8px; padding: 16px; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>KRX Access Diagnosis</h1>
  <p class="note">Secret values are not printed. This report only shows whether required keys are present and whether probes worked.</p>
  <div class="grid">
    {metric("KRX Open API", status["KRX Open API"])}
    {metric("Data Marketplace", status["KRX Data Marketplace"])}
    {metric("Investor Flow", status["Investor Flow"])}
    {metric("Short Sale", status["Short Sale"])}
  </div>
  <h2>Next Action</h2>
  <p>{escape(str(summary.get("next_action", "")))}</p>
  <h2>Details</h2>
  <pre>{escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def short_error(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:220] if text else exc.__class__.__name__


if __name__ == "__main__":
    raise SystemExit(main())
