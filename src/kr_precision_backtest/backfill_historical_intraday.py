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
DEFAULT_CANDIDATES = PROGRAM_ROOT / "output" / "latest.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "data" / "historical_intraday" / "minute_bars"
DEFAULT_METADATA = PROGRAM_ROOT / "data" / "historical_intraday" / "candidates.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical KIS 1-minute bars for KR DayPilot candidates.")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument("--reference-start", default="")
    parser.add_argument("--reference-end", default="")
    parser.add_argument("--entry-start", default="")
    parser.add_argument("--entry-end", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--market-div", default="J")
    parser.add_argument("--input-hour", default="153000")
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--single-window", action="store_true")
    args = parser.parse_args()

    candidates = load_candidates(
        args.candidates,
        max_candidates=max(args.max_candidates, 1),
        tickers=args.tickers,
        reference_start=args.reference_start,
        reference_end=args.reference_end,
        entry_start=args.entry_start,
        entry_end=args.entry_end,
    )
    if candidates.empty:
        print("No historical intraday candidates to backfill.")
        return 2

    credentials = load_kis_credentials(args.env_file)
    client = KisClient(credentials=credentials, token_cache_path=DEFAULT_TOKEN_CACHE)

    rows = []
    collected = 0
    reused = 0
    failed = 0
    for candidate in candidates.to_dict("records"):
        ticker = str(candidate["ticker"]).zfill(6)
        entry_date = _compact_date(candidate["entry_date"])
        output_path = args.output / entry_date / f"{ticker}.csv"
        if output_path.exists() and not args.force:
            row_count = _csv_row_count(output_path)
            status = "skipped_existing" if row_count > 0 else "empty_existing"
            rows.append(_metadata_row(candidate, status=status, rows=row_count, path=output_path))
            reused += 1
            continue

        try:
            bars = collect_historical_bars(
                client,
                ticker=ticker,
                target_date=entry_date,
                market_div=args.market_div,
                input_hour=args.input_hour,
                single_window=args.single_window,
                sleep_seconds=max(args.sleep_seconds, 0.0),
            )
            if bars.empty:
                rows.append(_metadata_row(candidate, status="empty", rows=0, path=""))
                failed += 1
            else:
                path = write_bars(bars, args.output)
                rows.append(_metadata_row(candidate, status="collected", rows=len(bars), path=path))
                collected += 1
        except KisApiError as exc:
            rows.append(_metadata_row(candidate, status="kis_error", rows=0, path="", error=str(exc)))
            failed += 1
        time.sleep(max(args.sleep_seconds, 0.0))

    write_metadata(rows, args.metadata)
    print("Historical intraday backfill complete.")
    print(f"candidates={len(candidates)}, collected={collected}, reused={reused}, failed={failed}")
    print(f"metadata={args.metadata}")
    return 0 if collected + reused > 0 else 1


def load_candidates(
    path: Path,
    *,
    max_candidates: int,
    tickers: str,
    reference_start: str,
    reference_end: str,
    entry_start: str,
    entry_end: str,
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"ticker": str})
    required = {"reference_day", "entry_date", "ticker"}
    if not required.issubset(df.columns):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"Candidate file is missing required columns: {missing}")

    df = df.copy()
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    df["reference_day_compact"] = df["reference_day"].map(_compact_date)
    df["entry_date_compact"] = df["entry_date"].map(_compact_date)
    df = df[(df["entry_date_compact"].str.len() == 8) & (df["ticker"].str.len() == 6)].copy()

    clean_tickers = _clean_tickers(tickers)
    if clean_tickers:
        df = df[df["ticker"].isin(clean_tickers)].copy()
    df = _filter_date(df, "reference_day_compact", reference_start, reference_end)
    df = _filter_date(df, "entry_date_compact", entry_start, entry_end)
    if "rank" in df.columns:
        df["rank"] = pd.to_numeric(df["rank"], errors="coerce").fillna(999999)
    else:
        df["rank"] = 999999
    return (
        df.sort_values(["reference_day_compact", "rank", "ticker"], ascending=[False, True, True])
        .drop_duplicates(["entry_date_compact", "ticker"])
        .head(max_candidates)
        .reset_index(drop=True)
    )


def collect_historical_bars(
    client: KisClient,
    *,
    ticker: str,
    target_date: str,
    market_div: str,
    input_hour: str,
    single_window: bool,
    sleep_seconds: float,
) -> pd.DataFrame:
    target_date = _compact_date(target_date)
    if single_window:
        frame = client.fetch_historical_intraday_minutes(
            ticker,
            target_date=target_date,
            market_div=market_div,
            input_hour=input_hour,
            include_past=True,
        )
        return _only_target_date(frame, target_date)

    frames = []
    current_hour = _compact_time(input_hour)
    for _ in range(8):
        frame = client.fetch_historical_intraday_minutes(
            ticker,
            target_date=target_date,
            market_div=market_div,
            input_hour=current_hour,
            include_past=True,
        )
        if frame.empty:
            break
        frame = _only_target_date(frame, target_date)
        if frame.empty:
            break
        frames.append(frame)
        min_time = str(frame["time"].min())
        if min_time <= "090000":
            break
        current_hour = min_time
        time.sleep(sleep_seconds)

    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(["ticker", "date", "time"]).sort_values(["date", "time"])
    merged = _only_target_date(merged, target_date)
    return merged.reset_index(drop=True)


def write_bars(bars: pd.DataFrame, root: Path) -> Path:
    date = str(bars["date"].iloc[0])
    ticker = str(bars["ticker"].iloc[0]).zfill(6)
    output_dir = root / date
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{ticker}.csv"
    bars.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def write_metadata(rows: list[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_frame = pd.DataFrame(rows)
    if path.exists():
        old = pd.read_csv(path, dtype={"ticker": str, "entry_date": str})
        key = new_frame["entry_date"].astype(str) + ":" + new_frame["ticker"].astype(str)
        old_key = old["entry_date"].astype(str).map(_compact_date) + ":" + old["ticker"].astype(str).str.zfill(6)
        old = old[~old_key.isin(set(key))].copy()
        out = pd.concat([old, new_frame], ignore_index=True)
    else:
        out = new_frame
    out.to_csv(path, index=False, encoding="utf-8-sig")


def _metadata_row(candidate: dict[str, object], *, status: str, rows: int, path: str | Path, error: str = "") -> dict[str, object]:
    return {
        "reference_day": _compact_date(candidate.get("reference_day", "")),
        "entry_date": _compact_date(candidate.get("entry_date", "")),
        "rank": candidate.get("rank", ""),
        "ticker": str(candidate.get("ticker", "")).zfill(6),
        "company": candidate.get("company", ""),
        "market": candidate.get("market", ""),
        "signal_score": candidate.get("signal_score", ""),
        "day_change_pct": candidate.get("day_change_pct", ""),
        "market_median_change_pct": candidate.get("market_median_change_pct", ""),
        "value_ratio_20": candidate.get("value_ratio_20", ""),
        "close_location_pct": candidate.get("close_location_pct", ""),
        "lower_tail_recovery_pct": candidate.get("lower_tail_recovery_pct", ""),
        "close_vs_open_pct": candidate.get("close_vs_open_pct", ""),
        "distance_from_60d_high_pct": candidate.get("distance_from_60d_high_pct", ""),
        "entry_gap_pct": candidate.get("entry_gap_pct", ""),
        "status": status,
        "rows": rows,
        "path": str(path),
        "error": error,
        "updated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
    }


def _filter_date(df: pd.DataFrame, column: str, start: str, end: str) -> pd.DataFrame:
    start_clean = _compact_date(start)
    end_clean = _compact_date(end)
    if start_clean:
        df = df[df[column] >= start_clean].copy()
    if end_clean:
        df = df[df[column] <= end_clean].copy()
    return df


def _clean_tickers(value: str) -> list[str]:
    result = []
    for item in str(value).split(","):
        digits = "".join(ch for ch in item if ch.isdigit())
        if not digits:
            continue
        ticker = digits.zfill(6)
        if len(ticker) == 6 and ticker not in result:
            result.append(ticker)
    return result


def _only_target_date(frame: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    target = _compact_date(target_date)
    if not target or "date" not in frame.columns:
        return frame.copy()
    return frame[frame["date"].astype(str).map(_compact_date) == target].copy()


def _compact_date(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text[:8] if len(text) >= 8 else ""


def _compact_time(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text.zfill(6)[:6] if text else "153000"


def _csv_row_count(path: Path) -> int:
    try:
        return max(sum(1 for _ in path.open("r", encoding="utf-8-sig")) - 1, 0)
    except OSError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
