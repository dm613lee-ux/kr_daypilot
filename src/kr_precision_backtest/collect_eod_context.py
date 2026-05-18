from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime
from html import escape
import io
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

import pandas as pd

from .collect_risk_context import DartClient, keyword_hits, request_json, short_error
from .env_config import load_env_file


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = PROGRAM_ROOT / ".env"
DEFAULT_SWING_RESULTS = PROGRAM_ROOT / "output" / "swing_backtest" / "latest.csv"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_REFERENCE_DIR = PROGRAM_ROOT / "data" / "reference"
DEFAULT_OUTPUT = PROGRAM_ROOT / "data" / "eod_context"
DEFAULT_REPORT_OUTPUT = PROGRAM_ROOT / "output" / "eod_context"

INVESTOR_COLUMNS = [
    "source_bas_dt",
    "ticker",
    "foreign_net_buy_value",
    "institution_net_buy_value",
    "retail_net_buy_value",
    "source",
    "updated_at",
]
SHORT_COLUMNS = [
    "source_bas_dt",
    "ticker",
    "short_sale_value",
    "short_sale_total_value",
    "short_sale_value_ratio",
    "short_balance_value",
    "short_balance_ratio",
    "credit_balance_ratio",
    "source",
    "updated_at",
]
DISCLOSURE_COLUMNS = [
    "source_bas_dt",
    "ticker",
    "corp_code",
    "receipt_no",
    "receipt_dt",
    "title",
    "event_type",
    "risk_flag",
    "source_url",
    "source",
    "updated_at",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect EOD investor flow, disclosure, short/credit context for KR DayPilot.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--swing-results", type=Path, default=DEFAULT_SWING_RESULTS)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.08)
    parser.add_argument("--skip-krx", action="store_true")
    parser.add_argument("--skip-dart", action="store_true")
    args = parser.parse_args()

    env = load_env_file(args.env_file)
    tickers, start_date, end_date = resolve_scope(
        args.swing_results,
        args.universe,
        from_date=args.from_date,
        to_date=args.to_date,
        max_tickers=args.max_tickers,
    )
    if not tickers:
        print("No tickers found for EOD context collection.")
        return 2

    output_paths, summary = collect_eod_context(
        tickers,
        start_date=start_date,
        end_date=end_date,
        env=env,
        reference_dir=args.reference_dir,
        output_dir=args.output,
        report_output=args.report_output,
        sleep_seconds=max(args.sleep_seconds, 0.0),
        skip_krx=args.skip_krx,
        skip_dart=args.skip_dart,
    )

    print("KR DayPilot EOD context collection complete.")
    print(f"Tickers: {summary['tickers']}")
    print(f"Date range: {summary['start_date']} ~ {summary['end_date']}")
    print(f"Investor rows: {summary['investor_flow_rows']}")
    print(f"Short/Credit rows: {summary['short_credit_rows']}")
    print(f"Disclosure rows: {summary['disclosure_rows']}")
    print(f"Status HTML: {output_paths['html']}")
    return 0


def resolve_scope(
    swing_results_path: Path,
    universe_path: Path,
    *,
    from_date: str,
    to_date: str,
    max_tickers: int,
) -> tuple[list[str], str, str]:
    tickers: list[str] = []
    dates: list[str] = []
    if swing_results_path.exists():
        frame = pd.read_csv(swing_results_path, dtype={"ticker": str}).fillna("")
        if "ticker" in frame.columns:
            tickers = [normalize_ticker(x) for x in frame["ticker"].tolist()]
        if "reference_day" in frame.columns:
            dates = [normalize_date(x) for x in frame["reference_day"].tolist()]
    if not tickers and universe_path.exists():
        universe = pd.read_csv(universe_path, dtype={"ticker": str}).fillna("")
        tickers = [normalize_ticker(x) for x in universe.get("ticker", pd.Series(dtype=str)).tolist()]
    tickers = sorted({ticker for ticker in tickers if ticker})
    if max_tickers > 0:
        tickers = tickers[:max_tickers]

    dates = sorted({date for date in dates if date})
    start_date = normalize_date(from_date) or (dates[0] if dates else datetime.now(tz=KST).strftime("%Y%m%d"))
    end_date = normalize_date(to_date) or (dates[-1] if dates else start_date)
    return tickers, start_date, end_date


def collect_eod_context(
    tickers: list[str],
    *,
    start_date: str,
    end_date: str,
    env: dict[str, str],
    reference_dir: Path,
    output_dir: Path,
    report_output: Path,
    sleep_seconds: float,
    skip_krx: bool,
    skip_dart: bool,
) -> tuple[dict[str, Path], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    investor_path = output_dir / "investor_flows.csv"
    short_path = output_dir / "short_credit.csv"
    disclosure_path = output_dir / "disclosures.csv"
    updated_at = datetime.now(tz=KST).isoformat(timespec="seconds")
    investor_status: dict[str, Any] = {"status": "skipped"}
    short_status: dict[str, Any] = {"status": "skipped"}
    disclosure_status: dict[str, Any] = {"status": "skipped"}

    if skip_krx:
        investor = read_existing_context(investor_path, INVESTOR_COLUMNS)
        short_credit = read_existing_context(short_path, SHORT_COLUMNS)
        investor_status = {"status": "skipped", "reason": "skip_krx", "preserved_rows": int(len(investor))}
        short_status = {"status": "skipped", "reason": "skip_krx", "preserved_rows": int(len(short_credit))}
    elif not (env.get("KRX_ID", "").strip() and env.get("KRX_PW", "").strip()):
        investor = empty_frame(INVESTOR_COLUMNS)
        short_credit = empty_frame(SHORT_COLUMNS)
        investor_status = {"status": "unavailable", "reason": "missing_krx_login_env"}
        short_status = {"status": "unavailable", "reason": "missing_krx_login_env"}
    else:
        configure_krx_login_env(env)
        investor, investor_status = collect_investor_flows_with_pykrx(
            tickers,
            start_date=start_date,
            end_date=end_date,
            updated_at=updated_at,
            sleep_seconds=sleep_seconds,
        )
        short_credit, short_status = collect_short_credit_with_pykrx(
            tickers,
            start_date=start_date,
            end_date=end_date,
            updated_at=updated_at,
            sleep_seconds=sleep_seconds,
        )

    if skip_dart:
        disclosures = read_existing_context(disclosure_path, DISCLOSURE_COLUMNS)
        disclosure_status = {"status": "skipped", "reason": "skip_dart", "preserved_rows": int(len(disclosures))}
    else:
        disclosures, disclosure_status = collect_disclosures(
            tickers,
            start_date=start_date,
            end_date=end_date,
            api_key=env.get("OPENDART_API_KEY", ""),
            reference_dir=reference_dir,
            updated_at=updated_at,
            sleep_seconds=sleep_seconds,
        )

    investor.to_csv(investor_path, index=False, encoding="utf-8-sig")
    short_credit.to_csv(short_path, index=False, encoding="utf-8-sig")
    disclosures.to_csv(disclosure_path, index=False, encoding="utf-8-sig")

    summary = {
        "generated_at": updated_at,
        "start_date": start_date,
        "end_date": end_date,
        "tickers": len(tickers),
        "investor_flow_rows": int(len(investor)),
        "short_credit_rows": int(len(short_credit)),
        "disclosure_rows": int(len(disclosures)),
        "investor_status": investor_status,
        "short_credit_status": short_status,
        "disclosure_status": disclosure_status,
        "paths": {
            "investor_flows": str(investor_path),
            "short_credit": str(short_path),
            "disclosures": str(disclosure_path),
        },
    }
    report_paths = write_report(summary, report_output)
    return {**summary["paths"], **report_paths}, summary


def configure_krx_login_env(env: dict[str, str]) -> None:
    for key in ["KRX_ID", "KRX_PW"]:
        value = env.get(key, "").strip()
        if value and not os.environ.get(key):
            os.environ[key] = value


def collect_investor_flows_with_pykrx(
    tickers: list[str],
    *,
    start_date: str,
    end_date: str,
    updated_at: str,
    sleep_seconds: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    stock, import_error = load_pykrx_stock()
    if stock is None:
        return empty_frame(INVESTOR_COLUMNS), {"status": "unavailable", "reason": import_error}

    probe, probe_error = safe_pykrx_call(lambda: stock.get_market_trading_value_by_date(start_date, end_date, tickers[0]))
    if probe_error or probe.empty:
        return empty_frame(INVESTOR_COLUMNS), {"status": "unavailable", "reason": probe_error or "empty_probe"}

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for ticker in tickers:
        frame, error = safe_pykrx_call(lambda t=ticker: stock.get_market_trading_value_by_date(start_date, end_date, t))
        if error:
            errors.append(f"{ticker}: {error}")
            continue
        if frame.empty:
            continue
        normalized = frame.reset_index().rename(columns={"날짜": "source_bas_dt"})
        for _, row in normalized.iterrows():
            rows.append(
                {
                    "source_bas_dt": normalize_date(row.get("source_bas_dt", "")),
                    "ticker": ticker,
                    "foreign_net_buy_value": number(row.get("외국인합계")),
                    "institution_net_buy_value": number(row.get("기관합계")),
                    "retail_net_buy_value": number(row.get("개인")),
                    "source": "pykrx-krx",
                    "updated_at": updated_at,
                }
            )
        time.sleep(sleep_seconds)
    frame = pd.DataFrame(rows, columns=INVESTOR_COLUMNS)
    return frame, {"status": "ok" if not frame.empty else "empty", "errors": errors[:5], "error_count": len(errors)}


def collect_short_credit_with_pykrx(
    tickers: list[str],
    *,
    start_date: str,
    end_date: str,
    updated_at: str,
    sleep_seconds: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    stock, import_error = load_pykrx_stock()
    if stock is None:
        return empty_frame(SHORT_COLUMNS), {"status": "unavailable", "reason": import_error}

    probe, probe_error = safe_pykrx_call(lambda: stock.get_shorting_value_by_date(start_date, end_date, tickers[0]))
    if probe_error or probe.empty:
        return empty_frame(SHORT_COLUMNS), {"status": "unavailable", "reason": probe_error or "empty_probe"}

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for ticker in tickers:
        short_value, value_error = safe_pykrx_call(lambda t=ticker: stock.get_shorting_value_by_date(start_date, end_date, t))
        short_balance, balance_error = safe_pykrx_call(lambda t=ticker: stock.get_shorting_balance_by_date(start_date, end_date, t))
        if value_error:
            errors.append(f"{ticker} short_value: {value_error}")
            short_value = pd.DataFrame()
        if balance_error:
            errors.append(f"{ticker} short_balance: {balance_error}")
            short_balance = pd.DataFrame()
        merged = merge_short_frames(short_value, short_balance)
        for _, row in merged.iterrows():
            rows.append(
                {
                    "source_bas_dt": normalize_date(row.get("source_bas_dt", "")),
                    "ticker": ticker,
                    "short_sale_value": number(row.get("short_sale_value")),
                    "short_sale_total_value": number(row.get("short_sale_total_value")),
                    "short_sale_value_ratio": number(row.get("short_sale_value_ratio")),
                    "short_balance_value": number(row.get("short_balance_value")),
                    "short_balance_ratio": number(row.get("short_balance_ratio")),
                    "credit_balance_ratio": 0.0,
                    "source": "pykrx-krx",
                    "updated_at": updated_at,
                }
            )
        time.sleep(sleep_seconds)
    frame = pd.DataFrame(rows, columns=SHORT_COLUMNS)
    return frame, {"status": "ok" if not frame.empty else "empty", "errors": errors[:5], "error_count": len(errors)}


def collect_disclosures(
    tickers: list[str],
    *,
    start_date: str,
    end_date: str,
    api_key: str,
    reference_dir: Path,
    updated_at: str,
    sleep_seconds: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not api_key.strip():
        return empty_frame(DISCLOSURE_COLUMNS), {"status": "skipped", "reason": "missing_opendart_key"}
    client = DartClient(api_key, reference_dir)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    missing_corp = 0
    for ticker in tickers:
        corp_code = client.corp_code_for_ticker(ticker)
        if not corp_code:
            missing_corp += 1
            continue
        try:
            body = request_json(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    "bgn_de": start_date,
                    "end_de": end_date,
                    "sort": "date",
                    "sort_mth": "desc",
                    "page_no": "1",
                    "page_count": "100",
                },
            )
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{ticker}: {short_error(exc)}")
            continue
        status = str(body.get("status") or "")
        if status == "013":
            time.sleep(sleep_seconds)
            continue
        if status and status != "000":
            errors.append(f"{ticker}: {body.get('message') or status}")
            continue
        for item in body.get("list", []) or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("report_nm", ""))
            hits = keyword_hits([title])
            receipt_no = str(item.get("rcept_no", ""))
            rows.append(
                {
                    "source_bas_dt": normalize_date(item.get("rcept_dt", "")),
                    "ticker": ticker,
                    "corp_code": corp_code,
                    "receipt_no": receipt_no,
                    "receipt_dt": normalize_date(item.get("rcept_dt", "")),
                    "title": title,
                    "event_type": classify_disclosure(title),
                    "risk_flag": bool(hits),
                    "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}" if receipt_no else "",
                    "source": "opendart",
                    "updated_at": updated_at,
                }
            )
        time.sleep(sleep_seconds)
    frame = pd.DataFrame(rows, columns=DISCLOSURE_COLUMNS)
    return frame, {
        "status": "ok" if not frame.empty else "empty",
        "missing_corp_code": missing_corp,
        "errors": errors[:5],
        "error_count": len(errors),
    }


def load_pykrx_stock() -> tuple[Any | None, str]:
    try:
        with redirect_stdout(io.StringIO()):
            from pykrx import stock  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local installation/network package state
        return None, short_error(exc)
    return stock, ""


def safe_pykrx_call(fn: Any) -> tuple[pd.DataFrame, str]:
    try:
        with redirect_stdout(io.StringIO()):
            frame = fn()
    except Exception as exc:
        return pd.DataFrame(), short_error(exc)
    if not isinstance(frame, pd.DataFrame):
        return pd.DataFrame(), "pykrx returned non-dataframe"
    return frame, ""


def merge_short_frames(short_value: pd.DataFrame, short_balance: pd.DataFrame) -> pd.DataFrame:
    value = normalize_short_value_frame(short_value)
    balance = normalize_short_balance_frame(short_balance)
    if value.empty and balance.empty:
        return pd.DataFrame(columns=["source_bas_dt"])
    if value.empty:
        return balance
    if balance.empty:
        return value
    return value.merge(balance, on="source_bas_dt", how="outer")


def normalize_short_value_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["source_bas_dt", "short_sale_value", "short_sale_total_value", "short_sale_value_ratio"])
    out = frame.reset_index().rename(
        columns={
            "날짜": "source_bas_dt",
            "공매도": "short_sale_value",
            "매수": "short_sale_total_value",
            "비중": "short_sale_value_ratio",
        }
    )
    keep = ["source_bas_dt", "short_sale_value", "short_sale_total_value", "short_sale_value_ratio"]
    return out[[col for col in keep if col in out.columns]].copy()


def normalize_short_balance_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["source_bas_dt", "short_balance_value", "short_balance_ratio"])
    out = frame.reset_index().rename(
        columns={
            "날짜": "source_bas_dt",
            "공매도금액": "short_balance_value",
            "비중": "short_balance_ratio",
        }
    )
    keep = ["source_bas_dt", "short_balance_value", "short_balance_ratio"]
    return out[[col for col in keep if col in out.columns]].copy()


def write_report(summary: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"eod_context_summary_{stamp}.json"
    html_path = output_dir / f"eod_context_summary_{stamp}.html"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"
    payload = {"summary": summary}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"json": json_path, "html": html_path, "latest_json": latest_json, "latest_html": latest_html}


def render_html(summary: dict[str, Any]) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot EOD Context Collection</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 24px; font-weight: 700; margin-top: 8px; }}
    pre {{ background: #f8fafc; border: 1px solid #eaecf0; border-radius: 8px; padding: 16px; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>EOD Context Collection</h1>
  <div class="grid">
    {metric("Tickers", summary.get("tickers", 0))}
    {metric("Investor rows", summary.get("investor_flow_rows", 0))}
    {metric("Short/Credit rows", summary.get("short_credit_rows", 0))}
    {metric("Disclosure rows", summary.get("disclosure_rows", 0))}
  </div>
  <p>{escape(str(summary.get("start_date", "")))} ~ {escape(str(summary.get("end_date", "")))}</p>
  <h2>Status</h2>
  <pre>{escape(json.dumps({k: summary.get(k) for k in ["investor_status", "short_credit_status", "disclosure_status", "paths"]}, ensure_ascii=False, indent=2))}</pre>
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def classify_disclosure(title: str) -> str:
    text = title.lower()
    if any(term in text for term in ["유상증자", "전환사채", "cb", "bw"]):
        return "financing_risk"
    if any(term in text for term in ["소송", "횡령", "배임", "감사의견", "거래정지", "불성실"]):
        return "governance_risk"
    if any(term in text for term in ["실적", "영업", "매출", "손익"]):
        return "earnings"
    if any(term in text for term in ["계약", "공급"]):
        return "contract"
    return "disclosure"


def empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def read_existing_context(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return empty_frame(columns)
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str}).fillna("")
    except (OSError, pd.errors.EmptyDataError, UnicodeError):
        return empty_frame(columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    return frame[columns].copy()


def normalize_ticker(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text.zfill(6) if text else ""


def normalize_date(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text[:8] if len(text) >= 8 else ""


def number(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_index_date(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.reset_index().copy()
    date_col = find_column(out.columns, ["날짜", "date"])
    if date_col:
        out = out.rename(columns={date_col: "source_bas_dt"})
    elif len(out.columns) > 0:
        out = out.rename(columns={out.columns[0]: "source_bas_dt"})
    return out


def find_column(columns: Any, names: list[str]) -> str:
    text_columns = [str(column) for column in columns]
    for name in names:
        for column in text_columns:
            if column == name:
                return column
    for name in names:
        lowered = name.lower()
        for column in text_columns:
            if lowered and lowered in column.lower():
                return column
    return ""


def row_pick(row: pd.Series, names: list[str]) -> object:
    for name in names:
        if name in row.index:
            return row.get(name)
    match = find_column(row.index, names)
    return row.get(match) if match else 0.0


# Keep these clean definitions below the legacy ones so Python binds the
# pykrx Korean-column parser to readable column names at runtime.
def collect_investor_flows_with_pykrx(
    tickers: list[str],
    *,
    start_date: str,
    end_date: str,
    updated_at: str,
    sleep_seconds: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    stock, import_error = load_pykrx_stock()
    if stock is None:
        return empty_frame(INVESTOR_COLUMNS), {"status": "unavailable", "reason": import_error}

    probe, probe_error = safe_pykrx_call(lambda: stock.get_market_trading_value_by_date(start_date, end_date, tickers[0]))
    if probe_error or probe.empty:
        return empty_frame(INVESTOR_COLUMNS), {"status": "unavailable", "reason": probe_error or "empty_probe"}

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for ticker in tickers:
        frame, error = safe_pykrx_call(lambda t=ticker: stock.get_market_trading_value_by_date(start_date, end_date, t))
        if error:
            errors.append(f"{ticker}: {error}")
            continue
        if frame.empty:
            continue
        normalized = normalize_index_date(frame)
        for _, row in normalized.iterrows():
            rows.append(
                {
                    "source_bas_dt": normalize_date(row.get("source_bas_dt", "")),
                    "ticker": ticker,
                    "foreign_net_buy_value": number(row_pick(row, ["외국인합계", "외국인"])),
                    "institution_net_buy_value": number(row_pick(row, ["기관합계", "기관"])),
                    "retail_net_buy_value": number(row_pick(row, ["개인"])),
                    "source": "pykrx-krx",
                    "updated_at": updated_at,
                }
            )
        time.sleep(sleep_seconds)
    frame = pd.DataFrame(rows, columns=INVESTOR_COLUMNS)
    return frame, {"status": "ok" if not frame.empty else "empty", "errors": errors[:5], "error_count": len(errors)}


def normalize_short_value_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["source_bas_dt", "short_sale_value", "short_sale_total_value", "short_sale_value_ratio"])
    out = normalize_index_date(frame)
    out["short_sale_value"] = out.apply(lambda row: row_pick(row, ["공매도"]), axis=1)
    out["short_sale_total_value"] = out.apply(lambda row: row_pick(row, ["매수"]), axis=1)
    out["short_sale_value_ratio"] = out.apply(lambda row: row_pick(row, ["비중"]), axis=1)
    keep = ["source_bas_dt", "short_sale_value", "short_sale_total_value", "short_sale_value_ratio"]
    return out[keep].copy()


def normalize_short_balance_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["source_bas_dt", "short_balance_value", "short_balance_ratio"])
    out = normalize_index_date(frame)
    out["short_balance_value"] = out.apply(lambda row: row_pick(row, ["공매도금액", "공매도잔고"]), axis=1)
    out["short_balance_ratio"] = out.apply(lambda row: row_pick(row, ["비중"]), axis=1)
    keep = ["source_bas_dt", "short_balance_value", "short_balance_ratio"]
    return out[keep].copy()


def classify_disclosure(title: str) -> str:
    text = title.lower()
    if any(term in text for term in ["유상증자", "전환사채", "신주인수권", "cb", "bw"]):
        return "financing_risk"
    if any(term in text for term in ["소송", "횡령", "배임", "감사의견", "거래정지", "불성실"]):
        return "governance_risk"
    if any(term in text for term in ["실적", "영업", "매출", "이익", "손실"]):
        return "earnings"
    if any(term in text for term in ["계약", "공급"]):
        return "contract"
    return "disclosure"


if __name__ == "__main__":
    raise SystemExit(main())
