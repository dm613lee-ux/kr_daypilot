from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import pandas as pd

from .env_config import load_kis_credentials
from .kis_client import KisApiError, KisClient


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV = PROGRAM_ROOT / ".env"
DEFAULT_TOKEN_CACHE = PROGRAM_ROOT / "runtime" / "kis_token.json"
DEFAULT_OUTPUT = PROGRAM_ROOT / "data" / "intraday" / "minute_bars"
DEFAULT_LATEST_RESULTS = PROGRAM_ROOT / "output" / "latest.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect KIS same-day 1-minute bars for KR DayPilot.")
    parser.add_argument("--tickers", default="", help="Comma separated tickers. Example: 005930,000660")
    parser.add_argument("--max-tickers", type=int, default=2)
    parser.add_argument("--input-hour", default="153000")
    parser.add_argument("--single-window", action="store_true", help="Collect only one 30-row KIS window.")
    parser.add_argument("--market-div", default="J", help="J: KRX, NX: NXT, UN: integrated")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--latest-results", type=Path, default=DEFAULT_LATEST_RESULTS)
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    args = parser.parse_args()

    tickers = _resolve_tickers(args.tickers, args.latest_results, max_tickers=max(args.max_tickers, 1))
    if not tickers:
        print("No tickers to collect. Run 검증_실행.bat first or pass --tickers.")
        return 2

    credentials = load_kis_credentials(args.env_file)
    client = KisClient(credentials=credentials, token_cache_path=DEFAULT_TOKEN_CACHE)

    collected = 0
    failed = 0
    for ticker in tickers:
        try:
            bars = _collect_ticker_bars(
                client,
                ticker,
                market_div=args.market_div,
                input_hour=args.input_hour,
                single_window=args.single_window,
                sleep_seconds=max(args.sleep_seconds, 0.0),
            )
            if bars.empty:
                print(f"{ticker}: no minute bars returned")
                failed += 1
                continue
            path = _write_bars(bars, args.output)
            print(f"{ticker}: saved {len(bars)} bars -> {path}")
            collected += 1
        except KisApiError as exc:
            print(f"{ticker}: KIS error: {exc}")
            failed += 1
        time.sleep(max(args.sleep_seconds, 0.0))

    print(f"Intraday collection complete. collected={collected}, failed={failed}")
    return 0 if collected > 0 else 1


def _collect_ticker_bars(
    client: KisClient,
    ticker: str,
    *,
    market_div: str,
    input_hour: str,
    single_window: bool,
    sleep_seconds: float,
) -> pd.DataFrame:
    if single_window:
        return client.fetch_intraday_minutes(
            ticker,
            market_div=market_div,
            input_hour=input_hour,
            include_past=True,
        )

    frames: list[pd.DataFrame] = []
    for hour in _full_day_query_hours(input_hour):
        frame = client.fetch_intraday_minutes(
            ticker,
            market_div=market_div,
            input_hour=hour,
            include_past=True,
        )
        if not frame.empty:
            frames.append(frame)
        time.sleep(sleep_seconds)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(["ticker", "date", "time"]).sort_values(["date", "time"])
    return merged.reset_index(drop=True)


def _full_day_query_hours(last_hour: str) -> list[str]:
    base = [
        "093000",
        "100000",
        "103000",
        "110000",
        "113000",
        "120000",
        "123000",
        "130000",
        "133000",
        "140000",
        "143000",
        "150000",
        "153000",
    ]
    compact = str(last_hour).replace(":", "").zfill(6)[:6]
    return [hour for hour in base if hour <= compact] or [compact]


def _resolve_tickers(tickers_arg: str, latest_results: Path, *, max_tickers: int) -> list[str]:
    if tickers_arg.strip():
        return _clean_tickers(tickers_arg.split(","))[:max_tickers]
    if not latest_results.exists():
        return []
    latest = pd.read_csv(latest_results, dtype={"ticker": str})
    if "ticker" not in latest.columns:
        return []
    if "reference_day" in latest.columns:
        latest = latest[latest["reference_day"].astype(str) == latest["reference_day"].astype(str).max()].copy()
    if "rank" in latest.columns:
        latest["rank"] = pd.to_numeric(latest["rank"], errors="coerce").fillna(999999)
        latest = latest.sort_values("rank")
    return _clean_tickers(latest["ticker"].dropna().astype(str).tolist())[:max_tickers]


def _clean_tickers(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        ticker = "".join(ch for ch in str(value) if ch.isdigit()).zfill(6)
        if len(ticker) != 6 or ticker in seen:
            continue
        seen.add(ticker)
        result.append(ticker)
    return result


def _write_bars(bars: pd.DataFrame, root: Path) -> Path:
    date = str(bars["date"].iloc[0])
    ticker = str(bars["ticker"].iloc[0]).zfill(6)
    output_dir = root / date
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{ticker}.csv"
    bars.to_csv(path, index=False, encoding="utf-8-sig")
    latest_dir = root / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    bars.to_csv(latest_dir / f"{ticker}.csv", index=False, encoding="utf-8-sig")
    manifest = root / "latest_manifest.txt"
    manifest.write_text(f"updated_at={datetime.now(tz=KST).isoformat()}\ndate={date}\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
