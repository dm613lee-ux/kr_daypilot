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

from .collect_risk_context import DartClient, request_json, short_error
from .env_config import load_env_file
from .rg2_factor_engine import build_rebalance_signal_days


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = PROGRAM_ROOT / ".env"
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
DEFAULT_REFERENCE_DIR = PROGRAM_ROOT / "data" / "reference"
DEFAULT_OUTPUT = PROGRAM_ROOT / "data" / "fundamentals"
DEFAULT_REPORT_OUTPUT = PROGRAM_ROOT / "output" / "fundamentals"

OPENDART_FINANCIAL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
FUNDAMENTAL_COLUMNS = [
    "source_bas_dt",
    "ticker",
    "corp_code",
    "bsns_year",
    "reprt_code",
    "fs_div",
    "revenue",
    "operating_income",
    "net_income",
    "equity",
    "total_assets",
    "source",
    "updated_at",
]
VALUATION_COLUMNS = [
    "source_bas_dt",
    "ticker",
    "per",
    "pbr",
    "dividend_yield",
    "bps",
    "eps",
    "dps",
    "source",
    "updated_at",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect OpenDART and KRX fundamentals for KR DayPilot RG2.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--years", default="", help="Comma-separated business years. Defaults to price-history range.")
    parser.add_argument("--report-codes", default="11011", help="Comma-separated OpenDART report codes.")
    parser.add_argument("--fs-divs", default="CFS,OFS", help="Comma-separated OpenDART fs_div values to try in order.")
    parser.add_argument("--valuation-frequency", choices=["monthly", "weekly"], default="monthly")
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.08)
    parser.add_argument("--skip-dart", action="store_true")
    parser.add_argument("--skip-krx", action="store_true")
    args = parser.parse_args()

    env = load_env_file(args.env_file)
    tickers, days = resolve_scope(
        args.price_history,
        args.universe,
        from_date=args.from_date,
        to_date=args.to_date,
        max_tickers=args.max_tickers,
    )
    if not tickers:
        print("No tickers found for fundamental collection.")
        return 2

    years = parse_years(args.years) or derive_business_years(days)
    report_codes = parse_csv(args.report_codes)
    fs_divs = parse_csv(args.fs_divs) or ["CFS", "OFS"]
    output_paths, summary = collect_fundamentals(
        tickers,
        trading_days=days,
        env=env,
        years=years,
        report_codes=report_codes,
        fs_divs=fs_divs,
        reference_dir=args.reference_dir,
        output_dir=args.output,
        report_output=args.report_output,
        valuation_frequency=args.valuation_frequency,
        sleep_seconds=max(args.sleep_seconds, 0.0),
        skip_dart=args.skip_dart,
        skip_krx=args.skip_krx,
    )

    print("KR DayPilot fundamental collection complete.")
    print(f"Tickers: {summary['tickers']}")
    print(f"Business years: {','.join(summary['years'])}")
    print(f"OpenDART rows: {summary['fundamental_rows']}")
    print(f"KRX valuation rows: {summary['valuation_rows']}")
    print(f"Status HTML: {output_paths['html']}")
    return 0


def resolve_scope(
    price_history_path: Path,
    universe_path: Path,
    *,
    from_date: str,
    to_date: str,
    max_tickers: int,
) -> tuple[list[str], list[str]]:
    tickers: list[str] = []
    days: list[str] = []
    if price_history_path.exists():
        history = pd.read_csv(price_history_path, dtype={"ticker": str, "isin": str, "source_bas_dt": str}, low_memory=False).fillna("")
        if "ticker" in history.columns:
            tickers.extend(normalize_ticker(value) for value in history["ticker"].tolist())
        if "source_bas_dt" in history.columns:
            days.extend(normalize_date(value) for value in history["source_bas_dt"].tolist())
    if universe_path.exists():
        universe = pd.read_csv(universe_path, dtype={"ticker": str}).fillna("")
        if "ticker" in universe.columns:
            tickers.extend(normalize_ticker(value) for value in universe["ticker"].tolist())
    tickers = sorted({ticker for ticker in tickers if ticker})
    if max_tickers > 0:
        tickers = tickers[:max_tickers]

    start = normalize_date(from_date)
    end = normalize_date(to_date)
    days = sorted({day for day in days if day})
    if start:
        days = [day for day in days if day >= start]
    if end:
        days = [day for day in days if day <= end]
    return tickers, days


def collect_fundamentals(
    tickers: list[str],
    *,
    trading_days: list[str],
    env: dict[str, str],
    years: list[str],
    report_codes: list[str],
    fs_divs: list[str],
    reference_dir: Path,
    output_dir: Path,
    report_output: Path,
    valuation_frequency: str,
    sleep_seconds: float,
    skip_dart: bool,
    skip_krx: bool,
) -> tuple[dict[str, Path], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    fundamental_path = output_dir / "fundamental_snapshots.csv"
    valuation_path = output_dir / "krx_valuation.csv"
    updated_at = datetime.now(tz=KST).isoformat(timespec="seconds")

    existing_fundamentals = read_existing_frame(fundamental_path, FUNDAMENTAL_COLUMNS)
    existing_valuation = read_existing_frame(valuation_path, VALUATION_COLUMNS)

    if skip_dart:
        fundamentals = existing_fundamentals
        dart_status: dict[str, Any] = {"status": "skipped", "reason": "skip_dart", "preserved_rows": int(len(fundamentals))}
    else:
        new_fundamentals, dart_status = collect_opendart_fundamentals(
            tickers,
            api_key=env.get("OPENDART_API_KEY", ""),
            reference_dir=reference_dir,
            years=years,
            report_codes=report_codes,
            fs_divs=fs_divs,
            updated_at=updated_at,
            sleep_seconds=sleep_seconds,
            existing_keys=existing_dart_keys(existing_fundamentals),
        )
        fundamentals = merge_frames(
            existing_fundamentals,
            new_fundamentals,
            FUNDAMENTAL_COLUMNS,
            key_columns=["source_bas_dt", "ticker", "bsns_year", "reprt_code", "fs_div"],
        )

    if skip_krx:
        valuation = existing_valuation
        krx_status: dict[str, Any] = {"status": "skipped", "reason": "skip_krx", "preserved_rows": int(len(valuation))}
    else:
        new_valuation, krx_status = collect_krx_valuation(
            tickers,
            trading_days=trading_days,
            env=env,
            frequency=valuation_frequency,
            updated_at=updated_at,
            sleep_seconds=sleep_seconds,
            existing_keys=existing_valuation_keys(existing_valuation),
        )
        valuation = merge_frames(
            existing_valuation,
            new_valuation,
            VALUATION_COLUMNS,
            key_columns=["source_bas_dt", "ticker"],
        )

    fundamentals.to_csv(fundamental_path, index=False, encoding="utf-8-sig")
    valuation.to_csv(valuation_path, index=False, encoding="utf-8-sig")

    summary = {
        "generated_at": updated_at,
        "tickers": len(tickers),
        "years": years,
        "report_codes": report_codes,
        "fs_divs": fs_divs,
        "valuation_frequency": valuation_frequency,
        "trading_days": len(trading_days),
        "fundamental_rows": int(len(fundamentals)),
        "valuation_rows": int(len(valuation)),
        "dart_status": dart_status,
        "krx_status": krx_status,
        "paths": {
            "fundamental_snapshots": str(fundamental_path),
            "krx_valuation": str(valuation_path),
        },
    }
    report_paths = write_report(summary, report_output)
    return {**summary["paths"], **report_paths}, summary


def collect_opendart_fundamentals(
    tickers: list[str],
    *,
    api_key: str,
    reference_dir: Path,
    years: list[str],
    report_codes: list[str],
    fs_divs: list[str],
    updated_at: str,
    sleep_seconds: float,
    existing_keys: set[tuple[str, str, str]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    api_key = api_key.strip()
    if not api_key:
        return empty_frame(FUNDAMENTAL_COLUMNS), {"status": "skipped", "reason": "missing_opendart_key"}
    client = DartClient(api_key, reference_dir)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    missing_corp = 0
    empty_reports = 0
    skipped_existing = 0

    for ticker in tickers:
        corp_code = client.corp_code_for_ticker(ticker)
        if not corp_code:
            missing_corp += 1
            continue
        for bsns_year in years:
            for reprt_code in report_codes:
                if (normalize_ticker(ticker), str(bsns_year), str(reprt_code)) in existing_keys:
                    skipped_existing += 1
                    continue
                collected = False
                for fs_div in fs_divs:
                    try:
                        body = request_json(
                            OPENDART_FINANCIAL_URL,
                            params={
                                "crtfc_key": api_key,
                                "corp_code": corp_code,
                                "bsns_year": str(bsns_year),
                                "reprt_code": str(reprt_code),
                                "fs_div": str(fs_div),
                            },
                        )
                    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                        errors.append(f"{ticker} {bsns_year} {reprt_code} {fs_div}: {short_error(exc)}")
                        continue
                    status = str(body.get("status") or "")
                    if status == "013":
                        empty_reports += 1
                        time.sleep(sleep_seconds)
                        continue
                    if status and status != "000":
                        errors.append(f"{ticker} {bsns_year} {reprt_code} {fs_div}: {body.get('message') or status}")
                        time.sleep(sleep_seconds)
                        continue
                    row = extract_financial_snapshot(
                        ticker,
                        corp_code=corp_code,
                        bsns_year=str(bsns_year),
                        reprt_code=str(reprt_code),
                        fs_div=str(fs_div),
                        body=body,
                        updated_at=updated_at,
                    )
                    if has_any_financial_value(row):
                        rows.append(row)
                        collected = True
                        time.sleep(sleep_seconds)
                        break
                    empty_reports += 1
                    time.sleep(sleep_seconds)
                if not collected:
                    continue
    frame = pd.DataFrame(rows, columns=FUNDAMENTAL_COLUMNS)
    return frame, {
        "status": "ok" if not frame.empty else ("unchanged" if skipped_existing else "empty"),
        "missing_corp_code": missing_corp,
        "empty_reports": empty_reports,
        "skipped_existing": skipped_existing,
        "errors": errors[:5],
        "error_count": len(errors),
    }


def collect_krx_valuation(
    tickers: list[str],
    *,
    trading_days: list[str],
    env: dict[str, str],
    frequency: str,
    updated_at: str,
    sleep_seconds: float,
    existing_keys: set[tuple[str, str]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not trading_days:
        return empty_frame(VALUATION_COLUMNS), {"status": "skipped", "reason": "missing_price_history_days"}
    configure_krx_login_env(env)
    stock, import_error = load_pykrx_stock()
    if stock is None:
        return empty_frame(VALUATION_COLUMNS), {"status": "unavailable", "reason": import_error}

    signal_days = build_rebalance_signal_days(trading_days, frequency)
    wanted = set(str(ticker).zfill(6) for ticker in tickers)
    rows: list[pd.DataFrame] = []
    errors: list[str] = []
    skipped_existing_days = 0
    for day in signal_days:
        missing_tickers = {ticker for ticker in wanted if (day, ticker) not in existing_keys}
        if not missing_tickers:
            skipped_existing_days += 1
            continue
        frame, error = safe_pykrx_call(lambda d=day: stock.get_market_fundamental(d, market="ALL"))
        if error:
            errors.append(f"{day}: {error}")
            continue
        normalized = normalize_krx_fundamental_frame(frame, source_bas_dt=day, updated_at=updated_at)
        if not normalized.empty:
            normalized = normalized[normalized["ticker"].isin(missing_tickers)].copy()
            rows.append(normalized)
        time.sleep(sleep_seconds)
    valuation = pd.concat(rows, ignore_index=True) if rows else empty_frame(VALUATION_COLUMNS)
    return valuation, {
        "status": "ok" if not valuation.empty else ("unchanged" if skipped_existing_days else "empty"),
        "signal_days": len(signal_days),
        "skipped_existing_days": skipped_existing_days,
        "errors": errors[:5],
        "error_count": len(errors),
    }


def report_availability_date(bsns_year: str | int, reprt_code: str) -> str:
    year = int(str(bsns_year)[:4])
    code = str(reprt_code)
    if code == "11011":
        return f"{year + 1}0401"
    if code == "11013":
        return f"{year}0516"
    if code == "11012":
        return f"{year}0816"
    if code == "11014":
        return f"{year}1116"
    raise ValueError(f"Unsupported OpenDART report code: {reprt_code}")


def extract_financial_snapshot(
    ticker: str,
    *,
    corp_code: str,
    bsns_year: str,
    reprt_code: str,
    fs_div: str,
    body: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    values = {
        "revenue": pick_account_value(body, field="revenue"),
        "operating_income": pick_account_value(body, field="operating_income"),
        "net_income": pick_account_value(body, field="net_income"),
        "equity": pick_account_value(body, field="equity"),
        "total_assets": pick_account_value(body, field="total_assets"),
    }
    return {
        "source_bas_dt": report_availability_date(str(bsns_year), str(reprt_code)),
        "ticker": normalize_ticker(ticker),
        "corp_code": str(corp_code).zfill(8),
        "bsns_year": str(bsns_year),
        "reprt_code": str(reprt_code),
        "fs_div": str(fs_div),
        **values,
        "source": "opendart",
        "updated_at": updated_at,
    }


def pick_account_value(body: dict[str, Any], *, field: str) -> float | None:
    rows = [row for row in body.get("list", []) or [] if isinstance(row, dict)]
    best: tuple[int, float] | None = None
    for row in rows:
        priority = account_match_priority(row, field)
        if priority is None:
            continue
        value = account_value(row)
        if value is None:
            continue
        if best is None or priority < best[0]:
            best = (priority, value)
    return best[1] if best else None


def account_match_priority(row: dict[str, Any], field: str) -> int | None:
    account_id = str(row.get("account_id", "")).lower()
    account_nm = str(row.get("account_nm", "")).strip().lower()
    sj_div = str(row.get("sj_div", "")).upper()
    exact_ids = {
        "revenue": {"ifrs-full_revenue", "ifrs-full_revenuefromcontractswithcustomersexcludingassessedtax", "dart_operatingrevenue"},
        "operating_income": {"dart_operatingincomeloss", "ifrs-full_operatingincomeloss"},
        "net_income": {"ifrs-full_profitloss", "dart_profitloss"},
        "equity": {"ifrs-full_equity", "dart_totalequity"},
        "total_assets": {"ifrs-full_assets", "dart_totalassets"},
    }
    id_terms = {
        "revenue": ["revenue"],
        "operating_income": ["operatingincomeloss", "operatingincome"],
        "net_income": ["profitloss"],
        "equity": ["equity"],
        "total_assets": ["assets"],
    }
    name_terms = {
        "revenue": ["매출액", "영업수익", "수익"],
        "operating_income": ["영업이익"],
        "net_income": ["당기순이익", "분기순이익", "반기순이익"],
        "equity": ["자본총계", "자본 총계"],
        "total_assets": ["자산총계", "자산 총계"],
    }
    if field in {"revenue", "operating_income", "net_income"} and sj_div and sj_div not in {"IS", "CIS"}:
        return None
    if field in {"equity", "total_assets"} and sj_div and sj_div != "BS":
        return None
    if account_id in exact_ids[field]:
        return 0
    if any(term in account_id for term in id_terms[field]):
        return 1
    if any(term in account_nm for term in name_terms[field]):
        return 2
    return None


def account_value(row: dict[str, Any]) -> float | None:
    sj_div = str(row.get("sj_div", "")).upper()
    columns = ["thstrm_add_amount", "thstrm_amount"] if sj_div in {"IS", "CIS"} else ["thstrm_amount", "thstrm_add_amount"]
    for column in columns:
        value = parse_number(row.get(column))
        if value is not None:
            return value
    return None


def normalize_krx_fundamental_frame(raw: pd.DataFrame, *, source_bas_dt: str, updated_at: str) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return empty_frame(VALUATION_COLUMNS)
    frame = raw.copy()
    frame.index = frame.index.astype(str)
    rows: list[dict[str, Any]] = []
    for ticker, row in frame.iterrows():
        normalized_ticker = normalize_ticker(ticker)
        if not normalized_ticker:
            continue
        rows.append(
            {
                "source_bas_dt": normalize_date(source_bas_dt),
                "ticker": normalized_ticker,
                "per": parse_number(row_pick(row, ["PER", "per"])),
                "pbr": parse_number(row_pick(row, ["PBR", "pbr"])),
                "dividend_yield": parse_number(row_pick(row, ["DIV", "dividend_yield", "배당수익률"])),
                "bps": parse_number(row_pick(row, ["BPS", "bps"])),
                "eps": parse_number(row_pick(row, ["EPS", "eps"])),
                "dps": parse_number(row_pick(row, ["DPS", "dps"])),
                "source": "pykrx-krx",
                "updated_at": updated_at,
            }
        )
    return pd.DataFrame(rows, columns=VALUATION_COLUMNS)


def configure_krx_login_env(env: dict[str, str]) -> None:
    for key in ["KRX_ID", "KRX_PW"]:
        value = env.get(key, "").strip()
        if value and not os.environ.get(key):
            os.environ[key] = value


def load_pykrx_stock() -> tuple[Any | None, str]:
    try:
        with redirect_stdout(io.StringIO()):
            from pykrx import stock  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local installation
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


def write_report(summary: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"fundamentals_summary_{stamp}.json"
    html_path = output_dir / f"fundamentals_summary_{stamp}.html"
    latest_json = output_dir / "latest_summary.json"
    latest_html = output_dir / "latest.html"
    payload = {"summary": summary}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    json_path.write_text(text, encoding="utf-8")
    latest_json.write_text(text, encoding="utf-8")
    html = render_html(summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"json": json_path, "html": html_path, "latest_json": latest_json, "latest_html": latest_html}


def render_html(summary: dict[str, Any]) -> str:
    status_payload = {
        "dart_status": summary.get("dart_status", {}),
        "krx_status": summary.get("krx_status", {}),
        "paths": summary.get("paths", {}),
    }
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Fundamentals Collection</title>
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
  <h1>Fundamentals Collection</h1>
  <div class="grid">
    {metric("Tickers", summary.get("tickers", 0))}
    {metric("OpenDART rows", summary.get("fundamental_rows", 0))}
    {metric("KRX rows", summary.get("valuation_rows", 0))}
    {metric("Trading days", summary.get("trading_days", 0))}
  </div>
  <p>Generated at {escape(str(summary.get("generated_at", "")))}</p>
  <h2>Status</h2>
  <pre>{escape(json.dumps(status_payload, ensure_ascii=False, indent=2))}</pre>
</body>
</html>"""


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def merge_frames(existing: pd.DataFrame, new: pd.DataFrame, columns: list[str], *, key_columns: list[str]) -> pd.DataFrame:
    pieces = [frame for frame in [existing, new] if isinstance(frame, pd.DataFrame) and not frame.empty]
    if not pieces:
        return empty_frame(columns)
    merged = pd.concat(pieces, ignore_index=True)
    for column in columns:
        if column not in merged.columns:
            merged[column] = pd.NA
    merged["ticker"] = merged["ticker"].astype(str).str.zfill(6)
    if "source_bas_dt" in merged.columns:
        merged["source_bas_dt"] = merged["source_bas_dt"].map(normalize_date)
    merged = merged.drop_duplicates(subset=key_columns, keep="last")
    return merged[columns].sort_values([column for column in ["ticker", "source_bas_dt"] if column in columns]).reset_index(drop=True)


def existing_dart_keys(frame: pd.DataFrame) -> set[tuple[str, str, str]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return set()
    required = {"ticker", "bsns_year", "reprt_code"}
    if not required.issubset(frame.columns):
        return set()
    out: set[tuple[str, str, str]] = set()
    for row in frame[list(required)].itertuples(index=False):
        values = row._asdict()
        ticker = normalize_ticker(values.get("ticker", ""))
        year = str(values.get("bsns_year", "")).strip()
        code = str(values.get("reprt_code", "")).strip()
        if ticker and year and code:
            out.add((ticker, year, code))
    return out


def existing_valuation_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return set()
    required = {"source_bas_dt", "ticker"}
    if not required.issubset(frame.columns):
        return set()
    out: set[tuple[str, str]] = set()
    for row in frame[list(required)].itertuples(index=False):
        values = row._asdict()
        day = normalize_date(values.get("source_bas_dt", ""))
        ticker = normalize_ticker(values.get("ticker", ""))
        if day and ticker:
            out.add((day, ticker))
    return out


def read_existing_frame(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return empty_frame(columns)
    try:
        frame = pd.read_csv(path, dtype={"ticker": str, "source_bas_dt": str}).fillna("")
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeError):
        return empty_frame(columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[columns].copy()


def empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def has_any_financial_value(row: dict[str, Any]) -> bool:
    return any(row.get(column) is not None for column in ["revenue", "operating_income", "net_income", "equity", "total_assets"])


def derive_business_years(days: list[str]) -> list[str]:
    if not days:
        current_year = datetime.now(tz=KST).year
        return [str(current_year - 2), str(current_year - 1)]
    years = [int(day[:4]) for day in days if len(day) >= 4]
    if not years:
        current_year = datetime.now(tz=KST).year
        return [str(current_year - 2), str(current_year - 1)]
    start = max(min(years) - 2, 1999)
    end = max(years) - 1
    return [str(year) for year in range(start, end + 1)]


def parse_years(value: str) -> list[str]:
    years: list[str] = []
    for item in parse_csv(value):
        digits = "".join(ch for ch in item if ch.isdigit())
        if len(digits) >= 4:
            years.append(digits[:4])
    return sorted(set(years))


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()]


def normalize_ticker(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ""
    ticker = digits.zfill(6)
    return ticker if len(ticker) == 6 else ""


def normalize_date(value: object) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").replace(" ", "")
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def row_pick(row: pd.Series, names: list[str]) -> object:
    for name in names:
        if name in row.index:
            return row.get(name)
    lowered = {str(column).lower(): column for column in row.index}
    for name in names:
        column = lowered.get(name.lower())
        if column is not None:
            return row.get(column)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
