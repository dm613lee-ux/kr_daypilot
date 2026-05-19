from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from .investment_recommender import json_ready, normalize_date


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "investment_recommender_pipeline"


@dataclass(frozen=True)
class PipelineConfig:
    program_root: Path
    from_date: str
    to_date: str
    run_date: str
    price_source: str = "auto"
    price_max_tickers: int = 300
    eod_max_tickers: int = 100
    fundamental_max_tickers: int = 300
    recommendation_top: int = 20
    skip_eod_context: bool = False
    skip_fundamentals: bool = False
    allow_stale_data: bool = False


@dataclass(frozen=True)
class PipelineStep:
    name: str
    command: list[str]
    required: bool


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh KR DayPilot data and then run the investment recommender.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--run-date", default="")
    parser.add_argument("--price-source", choices=["auto", "fdr", "pykrx", "pykrx-bulk"], default="auto")
    parser.add_argument("--price-max-tickers", type=int, default=300, help="0 means all tickers.")
    parser.add_argument("--eod-max-tickers", type=int, default=100)
    parser.add_argument("--fundamental-max-tickers", type=int, default=300)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--skip-eod-context", action="store_true")
    parser.add_argument("--skip-fundamentals", action="store_true")
    parser.add_argument("--allow-stale-data", action="store_true")
    args = parser.parse_args()

    history = load_history(args.price_history)
    window = resolve_pipeline_window(history, from_date=args.from_date, to_date=args.to_date, run_date=args.run_date)
    config = PipelineConfig(
        program_root=PROGRAM_ROOT,
        from_date=window["from_date"],
        to_date=window["to_date"],
        run_date=window["run_date"],
        price_source=args.price_source,
        price_max_tickers=max(args.price_max_tickers, 0),
        eod_max_tickers=max(args.eod_max_tickers, 0),
        fundamental_max_tickers=max(args.fundamental_max_tickers, 0),
        recommendation_top=max(args.top, 1),
        skip_eod_context=args.skip_eod_context,
        skip_fundamentals=args.skip_fundamentals,
        allow_stale_data=args.allow_stale_data,
    )
    steps = build_pipeline_steps(config, python_executable=sys.executable)
    statuses = run_pipeline_steps(steps, cwd=PROGRAM_ROOT)
    write_pipeline_summary(statuses, config, args.output)
    failed_required = [status for status in statuses if status["required"] and status["returncode"] != 0]
    return 1 if failed_required else 0


def resolve_pipeline_window(
    history: pd.DataFrame,
    *,
    from_date: str,
    to_date: str,
    run_date: str,
) -> dict[str, str]:
    normalized_run_date = normalize_date(run_date) if run_date else datetime.now(tz=KST).strftime("%Y%m%d")
    normalized_to_date = normalize_date(to_date) if to_date else normalized_run_date
    normalized_from_date = normalize_date(from_date)
    if not normalized_from_date:
        latest = latest_history_day(history)
        if latest:
            latest_ts = pd.to_datetime(latest, format="%Y%m%d", errors="coerce")
            normalized_from_date = (latest_ts + pd.Timedelta(days=1)).strftime("%Y%m%d") if not pd.isna(latest_ts) else normalized_to_date
        else:
            normalized_from_date = normalized_to_date
    return {
        "from_date": normalized_from_date,
        "to_date": normalized_to_date,
        "run_date": normalized_run_date,
    }


def build_pipeline_steps(config: PipelineConfig, *, python_executable: str) -> list[PipelineStep]:
    steps = [
        PipelineStep(
            "price_refresh",
            [
                python_executable,
                "-m",
                "kr_precision_backtest.collect_price_history",
                "--source",
                config.price_source,
                "--start",
                config.from_date,
                "--end",
                config.to_date,
                "--max-tickers",
                str(config.price_max_tickers),
            ],
            True,
        )
    ]
    if not config.skip_eod_context:
        steps.append(
            PipelineStep(
                "eod_context",
                [
                    python_executable,
                    "-m",
                    "kr_precision_backtest.collect_eod_context",
                    "--from-date",
                    config.from_date,
                    "--to-date",
                    config.to_date,
                    "--max-tickers",
                    str(config.eod_max_tickers),
                ],
                False,
            )
        )
    if not config.skip_fundamentals:
        steps.append(
            PipelineStep(
                "fundamentals",
                [
                    python_executable,
                    "-m",
                    "kr_precision_backtest.collect_fundamentals",
                    "--from-date",
                    config.from_date,
                    "--to-date",
                    config.to_date,
                    "--valuation-frequency",
                    "weekly",
                    "--max-tickers",
                    str(config.fundamental_max_tickers),
                ],
                False,
            )
        )
    recommender_command = [
        python_executable,
        "-m",
        "kr_precision_backtest.run_investment_recommender",
        "--run-date",
        config.run_date,
        "--top",
        str(config.recommendation_top),
    ]
    if config.allow_stale_data:
        recommender_command.append("--allow-stale-data")
    steps.append(PipelineStep("investment_recommender", recommender_command, True))
    return steps


def run_pipeline_steps(steps: list[PipelineStep], *, cwd: Path) -> list[dict[str, object]]:
    statuses: list[dict[str, object]] = []
    for step in steps:
        print(f"\n[{step.name}] {' '.join(step.command)}")
        completed = subprocess.run(step.command, cwd=cwd)
        status = {
            "name": step.name,
            "command": step.command,
            "required": step.required,
            "returncode": int(completed.returncode),
        }
        statuses.append(status)
        if step.required and completed.returncode != 0:
            print(f"Required pipeline step failed: {step.name}")
            break
    return statuses


def write_pipeline_summary(statuses: list[dict[str, object]], config: PipelineConfig, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "latest_summary.json"
    payload = {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "config": serializable_config(config),
        "steps": statuses,
    }
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return path


def serializable_config(config: PipelineConfig) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in config.__dict__.items():
        payload[key] = str(value) if isinstance(value, Path) else value
    return payload


def load_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"ticker": str, "isin": str, "source_bas_dt": str}, low_memory=False).fillna("")


def latest_history_day(history: pd.DataFrame) -> str:
    if history.empty or "source_bas_dt" not in history.columns:
        return ""
    days = history["source_bas_dt"].map(normalize_date)
    return str(days.max()) if not days.empty else ""


if __name__ == "__main__":
    raise SystemExit(main())
