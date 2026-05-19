from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"
PRICE_COLUMNS = [
    "ticker",
    "company",
    "market",
    "isin",
    "source_bas_dt",
    "open",
    "high",
    "low",
    "close",
    "change",
    "day_change_pct",
    "volume",
    "trading_value",
    "listed_shares",
    "market_cap",
    "data_vendor",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh KR DayPilot price history from optional external data sources.")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--source", choices=["auto", "fdr", "pykrx", "pykrx-bulk"], default="auto")
    parser.add_argument("--start", default="", help="Start date YYYYMMDD. Defaults to latest local date + 1 calendar day.")
    parser.add_argument("--end", default="", help="End date YYYYMMDD. Defaults to today in Asia/Seoul.")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers. Defaults to investment universe.")
    parser.add_argument("--max-tickers", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    history = load_csv(args.history)
    universe = load_csv(args.universe)
    tickers = resolve_tickers(args.tickers, universe, history, max_tickers=max(args.max_tickers, 0))
    end = normalize_date(args.end) if args.end else datetime.now(tz=KST).strftime("%Y%m%d")
    start = normalize_date(args.start) if args.start else default_start_date(history, end)
    metadata = build_metadata(history, universe)

    rows, statuses = collect_external_price_rows(
        tickers,
        start=start,
        end=end,
        source=args.source,
        metadata=metadata,
    )
    merged = merge_price_history(history, rows)
    if not args.dry_run:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(args.output, index=False, encoding="utf-8-sig")

    ok = sum(1 for item in statuses if item.get("status") == "ok")
    failed = len(statuses) - ok
    print("KR DayPilot price history refresh complete.")
    print(f"Source: {args.source}")
    print(f"Window: {start} -> {end}")
    print(f"Tickers: {len(tickers)}")
    print(f"Fetched ok: {ok}")
    print(f"Failed/skipped: {failed}")
    print(f"New rows: {len(rows)}")
    print(f"Output rows: {len(merged)}")
    print(f"Output: {args.output}")
    if failed:
        for item in statuses[:10]:
            if item.get("status") != "ok":
                print(f"{item.get('ticker', '')}: {item.get('status')} {item.get('message', '')}")
    return 0 if ok or rows.empty else 1


def collect_external_price_rows(
    tickers: list[str],
    *,
    start: str,
    end: str,
    source: str,
    metadata: dict[str, dict[str, object]],
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    if not tickers:
        return pd.DataFrame(columns=PRICE_COLUMNS), []
    if is_inverted_date_window(start, end):
        return pd.DataFrame(columns=PRICE_COLUMNS), [
            {
                "source": source,
                "status": "up_to_date",
                "message": f"date window has no new trading days: {start} -> {end}",
            }
        ]
    if source in {"auto", "pykrx-bulk"}:
        bulk_rows, bulk_status = collect_pykrx_bulk(tickers, start=start, end=end, metadata=metadata)
        if not bulk_rows.empty or source == "pykrx-bulk":
            return bulk_rows, [bulk_status]
    sources = ["fdr", "pykrx"] if source == "auto" else [source]
    all_rows: list[pd.DataFrame] = []
    statuses: list[dict[str, object]] = []
    for ticker in tickers:
        attempts: list[dict[str, object]] = []
        for candidate in sources:
            frame, status = collect_one_source(candidate, ticker, start, end, metadata.get(ticker, {}))
            attempts.append(status)
            if not frame.empty:
                all_rows.append(frame)
                statuses.append(status)
                break
        else:
            statuses.extend(attempts)
    if not all_rows:
        return pd.DataFrame(columns=PRICE_COLUMNS), statuses
    return pd.concat(all_rows, ignore_index=True)[PRICE_COLUMNS], statuses


def is_inverted_date_window(start: str, end: str) -> bool:
    start_ts = pd.to_datetime(normalize_date(start), format="%Y%m%d", errors="coerce")
    end_ts = pd.to_datetime(normalize_date(end), format="%Y%m%d", errors="coerce")
    return not pd.isna(start_ts) and not pd.isna(end_ts) and start_ts > end_ts


def collect_pykrx_bulk(
    tickers: list[str],
    *,
    start: str,
    end: str,
    metadata: dict[str, dict[str, object]],
) -> tuple[pd.DataFrame, dict[str, object]]:
    try:
        from pykrx import stock  # type: ignore
    except ImportError:
        return pd.DataFrame(columns=PRICE_COLUMNS), {"source": "pykrx-bulk", "status": "missing_dependency", "message": "pykrx is not installed."}
    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    wanted = set(tickers)
    for day in calendar_days(start, end):
        try:
            ohlcv = stock.get_market_ohlcv_by_ticker(day, market="ALL")
            cap = stock.get_market_cap_by_ticker(day, market="ALL")
        except Exception as exc:
            errors.append(f"{day}: {exc}")
            continue
        normalized = normalize_pykrx_ticker_snapshot(ohlcv, cap, source_bas_dt=day, metadata=metadata)
        if wanted:
            normalized = normalized[normalized["ticker"].isin(wanted)].copy()
        if not normalized.empty:
            frames.append(normalized)
    if not frames:
        return pd.DataFrame(columns=PRICE_COLUMNS), {
            "source": "pykrx-bulk",
            "status": "empty_or_error",
            "rows": 0,
            "errors": errors[:5],
        }
    rows = pd.concat(frames, ignore_index=True)[PRICE_COLUMNS]
    return rows, {
        "source": "pykrx-bulk",
        "status": "ok",
        "rows": int(len(rows)),
        "days": int(rows["source_bas_dt"].nunique()),
        "errors": errors[:5],
    }


def collect_one_source(
    source: str,
    ticker: str,
    start: str,
    end: str,
    metadata: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    try:
        if source == "fdr":
            return collect_fdr(ticker, start, end, metadata)
        if source == "pykrx":
            return collect_pykrx(ticker, start, end, metadata)
    except Exception as exc:  # external source failures must not crash the whole refresh
        return pd.DataFrame(columns=PRICE_COLUMNS), {"ticker": ticker, "source": source, "status": "error", "message": str(exc)}
    return pd.DataFrame(columns=PRICE_COLUMNS), {"ticker": ticker, "source": source, "status": "unsupported_source", "message": source}


def collect_fdr(
    ticker: str,
    start: str,
    end: str,
    metadata: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    try:
        import FinanceDataReader as fdr  # type: ignore
    except ImportError:
        return pd.DataFrame(columns=PRICE_COLUMNS), {"ticker": ticker, "source": "fdr", "status": "missing_dependency", "message": "FinanceDataReader is not installed."}
    raw = fdr.DataReader(ticker, start, end)
    normalized = normalize_fdr_ohlcv(raw, ticker=ticker, metadata=metadata)
    return normalized, {"ticker": ticker, "source": "fdr", "status": "ok" if not normalized.empty else "empty", "rows": len(normalized)}


def collect_pykrx(
    ticker: str,
    start: str,
    end: str,
    metadata: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    try:
        from pykrx import stock  # type: ignore
    except ImportError:
        return pd.DataFrame(columns=PRICE_COLUMNS), {"ticker": ticker, "source": "pykrx", "status": "missing_dependency", "message": "pykrx is not installed."}
    ohlcv = stock.get_market_ohlcv_by_date(start, end, ticker)
    cap = stock.get_market_cap_by_date(start, end, ticker)
    normalized = normalize_pykrx_ohlcv(ohlcv, cap, ticker=ticker, metadata=metadata)
    return normalized, {"ticker": ticker, "source": "pykrx", "status": "ok" if not normalized.empty else "empty", "rows": len(normalized)}


def normalize_fdr_ohlcv(raw: pd.DataFrame, *, ticker: str, metadata: dict[str, object]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    frame = raw.copy()
    frame = frame.reset_index()
    rename = {
        "Date": "source_bas_dt",
        "날짜": "source_bas_dt",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "Change": "day_change_ratio",
    }
    frame = frame.rename(columns=rename)
    if "source_bas_dt" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "source_bas_dt"})
    frame["source_bas_dt"] = frame["source_bas_dt"].map(normalize_date)
    frame["ticker"] = str(ticker).zfill(6)
    frame["data_vendor"] = "FinanceDataReader"
    return finalize_price_frame(frame, metadata)


def normalize_pykrx_ohlcv(
    raw_ohlcv: pd.DataFrame,
    raw_cap: pd.DataFrame | None,
    *,
    ticker: str,
    metadata: dict[str, object],
) -> pd.DataFrame:
    if raw_ohlcv is None or raw_ohlcv.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    frame = raw_ohlcv.copy().reset_index()
    frame = frame.rename(
        columns={
            "날짜": "source_bas_dt",
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "등락률": "day_change_pct",
            "거래량": "volume",
            "거래대금": "trading_value",
        }
    )
    if "source_bas_dt" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "source_bas_dt"})
    if raw_cap is not None and not raw_cap.empty:
        cap = raw_cap.copy().reset_index()
        cap = cap.rename(
            columns={
                "날짜": "source_bas_dt",
                "시가총액": "market_cap",
                "상장주식수": "listed_shares",
                "거래대금": "trading_value",
            }
        )
        if "source_bas_dt" not in cap.columns:
            cap = cap.rename(columns={cap.columns[0]: "source_bas_dt"})
        cap["source_bas_dt"] = cap["source_bas_dt"].map(normalize_date)
        keep = [col for col in ["source_bas_dt", "market_cap", "listed_shares", "trading_value"] if col in cap.columns]
        frame["source_bas_dt"] = frame["source_bas_dt"].map(normalize_date)
        frame = frame.merge(cap[keep], on="source_bas_dt", how="left", suffixes=("", "_cap"))
        if "trading_value_cap" in frame.columns:
            frame["trading_value"] = frame.get("trading_value").fillna(frame["trading_value_cap"])
            frame = frame.drop(columns=["trading_value_cap"])
    frame["ticker"] = str(ticker).zfill(6)
    frame["data_vendor"] = "pykrx"
    return finalize_price_frame(frame, metadata)


def normalize_pykrx_ticker_snapshot(
    raw_ohlcv: pd.DataFrame,
    raw_cap: pd.DataFrame | None,
    *,
    source_bas_dt: str,
    metadata: dict[str, dict[str, object]],
) -> pd.DataFrame:
    if raw_ohlcv is None or raw_ohlcv.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    frame = raw_ohlcv.copy().reset_index()
    frame = frame.rename(
        columns={
            frame.columns[0]: "ticker",
            "티커": "ticker",
            "종목코드": "ticker",
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "등락률": "day_change_pct",
            "거래량": "volume",
            "거래대금": "trading_value",
        }
    )
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame["source_bas_dt"] = normalize_date(source_bas_dt)
    if raw_cap is not None and not raw_cap.empty:
        cap = raw_cap.copy().reset_index()
        cap = cap.rename(
            columns={
                cap.columns[0]: "ticker",
                "티커": "ticker",
                "종목코드": "ticker",
                "시가총액": "market_cap",
                "상장주식수": "listed_shares",
                "거래대금": "trading_value",
            }
        )
        cap["ticker"] = cap["ticker"].astype(str).str.zfill(6)
        keep = [col for col in ["ticker", "market_cap", "listed_shares", "trading_value"] if col in cap.columns]
        frame = frame.merge(cap[keep], on="ticker", how="left", suffixes=("", "_cap"))
        if "trading_value_cap" in frame.columns:
            frame["trading_value"] = frame.get("trading_value").fillna(frame["trading_value_cap"])
            frame = frame.drop(columns=["trading_value_cap"])
    frame["company"] = frame["ticker"].map(lambda ticker: str(metadata.get(ticker, {}).get("company", "")))
    frame["market"] = frame["ticker"].map(lambda ticker: str(metadata.get(ticker, {}).get("market", "")))
    frame["isin"] = frame["ticker"].map(lambda ticker: str(metadata.get(ticker, {}).get("isin", "")))
    frame["data_vendor"] = "pykrx-bulk"
    return finalize_price_frame(frame, {})


def finalize_price_frame(frame: pd.DataFrame, metadata: dict[str, object]) -> pd.DataFrame:
    result = frame.copy()
    for column in ["company", "market", "isin"]:
        if column not in result.columns:
            result[column] = str(metadata.get(column, ""))
        result[column] = result[column].fillna(str(metadata.get(column, "")))
    for column in ["open", "high", "low", "close", "volume", "trading_value", "listed_shares", "market_cap"]:
        if column not in result.columns:
            result[column] = pd.NA
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if "day_change_ratio" in result.columns and "day_change_pct" not in result.columns:
        result["day_change_pct"] = pd.to_numeric(result["day_change_ratio"], errors="coerce") * 100.0
    if "day_change_pct" not in result.columns:
        result["day_change_pct"] = pd.NA
    result["day_change_pct"] = pd.to_numeric(result["day_change_pct"], errors="coerce")
    if "change" not in result.columns:
        result["change"] = pd.NA
    result["change"] = pd.to_numeric(result["change"], errors="coerce")
    if result["trading_value"].isna().all():
        result["trading_value"] = result["close"] * result["volume"]
    if result["listed_shares"].isna().all() and metadata.get("listed_shares") not in ("", None):
        result["listed_shares"] = pd.to_numeric(pd.Series([metadata.get("listed_shares")] * len(result)), errors="coerce")
    missing_market_cap = result["market_cap"].isna()
    result.loc[missing_market_cap, "market_cap"] = result.loc[missing_market_cap, "close"] * result.loc[missing_market_cap, "listed_shares"]
    result["source_bas_dt"] = result["source_bas_dt"].map(normalize_date)
    result = result.dropna(subset=["ticker", "source_bas_dt", "open", "high", "low", "close"])
    result = result[result["close"] > 0].copy()
    for column in PRICE_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    return result[PRICE_COLUMNS].sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def merge_price_history(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    frames = [normalize_existing_history(existing)]
    if new_rows is not None and not new_rows.empty:
        frames.append(normalize_existing_history(new_rows))
    merged = pd.concat(frames, ignore_index=True)
    if merged.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    merged["ticker"] = merged["ticker"].astype(str).str.zfill(6)
    merged["source_bas_dt"] = merged["source_bas_dt"].map(normalize_date)
    merged = merged.drop_duplicates(["ticker", "source_bas_dt"], keep="last")
    return merged[PRICE_COLUMNS].sort_values(["source_bas_dt", "ticker"]).reset_index(drop=True)


def normalize_existing_history(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    result = frame.copy()
    for column in PRICE_COLUMNS:
        if column not in result.columns:
            result[column] = ""
    result["ticker"] = result["ticker"].astype(str).str.zfill(6)
    result["source_bas_dt"] = result["source_bas_dt"].map(normalize_date)
    return result[PRICE_COLUMNS]


def build_metadata(history: pd.DataFrame, universe: pd.DataFrame) -> dict[str, dict[str, object]]:
    metadata: dict[str, dict[str, object]] = {}
    for frame in [universe, latest_history_rows(history)]:
        if frame is None or frame.empty or "ticker" not in frame.columns:
            continue
        for row in frame.to_dict("records"):
            ticker = str(row.get("ticker", "")).zfill(6)
            if not ticker:
                continue
            current = metadata.setdefault(ticker, {})
            for key in ["company", "market", "isin", "listed_shares"]:
                value = row.get(key)
                if value not in ("", None) and not pd.isna(value):
                    current[key] = value
    return metadata


def latest_history_rows(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty or not {"ticker", "source_bas_dt"}.issubset(history.columns):
        return pd.DataFrame()
    frame = history.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.zfill(6)
    frame["source_bas_dt"] = frame["source_bas_dt"].map(normalize_date)
    return frame.sort_values(["ticker", "source_bas_dt"]).groupby("ticker", as_index=False).tail(1)


def resolve_tickers(tickers_arg: str, universe: pd.DataFrame, history: pd.DataFrame, *, max_tickers: int) -> list[str]:
    if tickers_arg.strip():
        tickers = clean_tickers(tickers_arg.split(","))
        return tickers[:max_tickers] if max_tickers > 0 else tickers
    source = prioritized_history_universe(history, universe)
    if source is None or source.empty or "ticker" not in source.columns:
        source = universe
    if source is None or source.empty or "ticker" not in source.columns:
        return []
    frame = source.copy()
    if "investment_universe" in frame.columns:
        frame = frame[frame["investment_universe"].fillna("Y").astype(str).str.upper().ne("N")]
    tickers = clean_tickers(frame["ticker"].tolist())
    return tickers[:max_tickers] if max_tickers > 0 else tickers


def prioritized_history_universe(history: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    latest = latest_history_rows(history)
    if latest.empty or "ticker" not in latest.columns:
        return universe
    frame = latest.copy()
    if universe is not None and not universe.empty and "ticker" in universe.columns:
        uni = universe.copy()
        uni["ticker"] = uni["ticker"].astype(str).str.zfill(6)
        keep = [col for col in ["ticker", "investment_universe"] if col in uni.columns]
        if keep:
            frame = frame.merge(uni[keep].drop_duplicates("ticker"), on="ticker", how="left")
    if "investment_universe" not in frame.columns:
        frame["investment_universe"] = "Y"
    if "market_cap" in frame.columns:
        frame["_market_cap_sort"] = pd.to_numeric(frame["market_cap"], errors="coerce").fillna(0.0)
        frame = frame.sort_values(["_market_cap_sort", "ticker"], ascending=[False, True]).drop(columns=["_market_cap_sort"])
    return frame


def clean_tickers(values: list[object]) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticker = str(value).strip().zfill(6)
        if ticker and ticker not in seen:
            tickers.append(ticker)
            seen.add(ticker)
    return tickers


def default_start_date(history: pd.DataFrame, end: str) -> str:
    if history is None or history.empty or "source_bas_dt" not in history.columns:
        return end
    latest = pd.to_datetime(history["source_bas_dt"].map(normalize_date), format="%Y%m%d", errors="coerce").max()
    if pd.isna(latest):
        return end
    return (latest + pd.Timedelta(days=1)).strftime("%Y%m%d")


def calendar_days(start: str, end: str) -> list[str]:
    start_ts = pd.to_datetime(normalize_date(start), format="%Y%m%d", errors="coerce")
    end_ts = pd.to_datetime(normalize_date(end), format="%Y%m%d", errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts) or start_ts > end_ts:
        return []
    return [day.strftime("%Y%m%d") for day in pd.date_range(start_ts, end_ts, freq="D")]


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"ticker": str, "isin": str, "source_bas_dt": str}, low_memory=False).fillna("")


def normalize_date(value: object) -> str:
    text = str(value).strip().replace("-", "").replace("/", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y%m%d")


if __name__ == "__main__":
    raise SystemExit(main())
