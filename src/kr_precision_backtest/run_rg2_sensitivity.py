from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .policy import load_policy, policy_to_dict
from .run_research_gate2 import (
    DEFAULT_EOD_CONTEXT,
    DEFAULT_EXPERIMENTS,
    DEFAULT_FUNDAMENTALS,
    DEFAULT_KRX_VALUATION,
    DEFAULT_POLICY,
    DEFAULT_PRICE_HISTORY,
    DEFAULT_UNIVERSE,
    build_strategy_profiles,
    prepare_history,
    run_research_gate2,
)
from .rg2_factor_engine import FactorConfig


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LATEST_OUTPUT = PROGRAM_ROOT / "output" / "research_gate2_sensitivity"
DEFAULT_REGISTRY = PROGRAM_ROOT / "experiments" / "registry_rg2_sensitivity.csv"


@dataclass(frozen=True)
class SensitivityScenario:
    scenario_id: str
    frequency: str
    portfolio_size: int
    slippage_pct: float
    round_trip_cost_pct: float
    max_periods: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RG2 sensitivity validation across frequency, size, and cost assumptions.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--eod-context-dir", type=Path, default=DEFAULT_EOD_CONTEXT)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--fundamentals", type=Path, default=DEFAULT_FUNDAMENTALS)
    parser.add_argument("--krx-valuation", type=Path, default=DEFAULT_KRX_VALUATION)
    parser.add_argument("--experiments-dir", type=Path, default=DEFAULT_EXPERIMENTS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--latest-output", type=Path, default=DEFAULT_LATEST_OUTPUT)
    parser.add_argument("--frequencies", default="monthly,weekly")
    parser.add_argument("--portfolio-sizes", default="10,20")
    parser.add_argument("--slippage-pcts", default="0.2,0.5")
    parser.add_argument("--round-trip-cost-pct", type=float, default=-1.0)
    parser.add_argument("--max-periods", type=int, default=36)
    parser.add_argument("--window-periods", type=int, default=12)
    parser.add_argument("--min-pass-rate-pct", type=float, default=60.0)
    parser.add_argument("--min-factor-groups", type=int, default=2)
    parser.add_argument("--min-fundamental-coverage-pct", type=float, default=50.0)
    parser.add_argument("--min-profitability-coverage-pct", type=float, default=50.0)
    parser.add_argument("--min-periods-for-paper", type=int, default=12)
    parser.add_argument("--min-positions-for-paper", type=int, default=80)
    parser.add_argument("--allow-risk-disclosure-penalty-only", action="store_true")
    args = parser.parse_args()

    policy = load_policy(args.policy)
    round_trip_cost_pct = (
        policy.backtest_round_trip_cost_default_pct
        if args.round_trip_cost_pct < 0
        else float(args.round_trip_cost_pct)
    )
    scenarios = build_scenarios(
        frequencies=parse_text_list(args.frequencies),
        portfolio_sizes=parse_int_list(args.portfolio_sizes),
        slippage_pcts=parse_float_list(args.slippage_pcts),
        round_trip_cost_pct=round_trip_cost_pct,
        max_periods=max(args.max_periods, 1),
    )
    if not scenarios:
        print("No sensitivity scenarios to run.")
        return 2

    base_config = FactorConfig(
        min_avg_value_20d_krw=policy.min_avg_trading_value_20d_krw,
        min_factor_groups=max(args.min_factor_groups, 1),
        block_risk_disclosures=not args.allow_risk_disclosure_penalty_only,
    )
    profiles = build_strategy_profiles(base_config)
    history, data_status = prepare_history(
        args.price_history,
        args.eod_context_dir,
        args.universe,
        args.fundamentals,
        args.krx_valuation,
    )
    result = run_sensitivity(
        history,
        profiles,
        scenarios,
        window_periods=max(args.window_periods, 1),
        min_fundamental_coverage_pct=max(args.min_fundamental_coverage_pct, 0.0),
        min_profitability_coverage_pct=max(args.min_profitability_coverage_pct, 0.0),
        min_periods_for_paper=max(args.min_periods_for_paper, 1),
        min_positions_for_paper=max(args.min_positions_for_paper, 1),
        min_pass_rate_pct=max(args.min_pass_rate_pct, 0.0),
        data_status=data_status,
    )
    paths = write_outputs(result, scenarios, policy_to_dict(policy), args=args)
    update_registry(args.registry, result["strategy_robustness"], paths["experiment_dir"], result["summary"])

    summary = result["summary"]
    print("KR DayPilot RG2 sensitivity validation complete.")
    print(f"Experiment: {paths['experiment_dir']}")
    print(f"Scenarios: {summary['scenarios']}")
    print(f"Robust strategies: {summary['robust_strategies']}")
    print(f"Best robust strategy: {summary['best_robust_strategy']}")
    print(f"HTML: {paths['html']}")
    return 0


def build_scenarios(
    *,
    frequencies: list[str],
    portfolio_sizes: list[int],
    slippage_pcts: list[float],
    round_trip_cost_pct: float,
    max_periods: int,
) -> list[SensitivityScenario]:
    valid_frequencies = [frequency for frequency in unique_text(frequencies) if frequency in {"monthly", "weekly"}]
    sizes = [size for size in unique_int(portfolio_sizes) if size > 0]
    slippages = [slippage for slippage in unique_float(slippage_pcts) if slippage >= 0]
    scenarios: list[SensitivityScenario] = []
    counter = 1
    for frequency in valid_frequencies:
        for portfolio_size in sizes:
            for slippage_pct in slippages:
                scenarios.append(
                    SensitivityScenario(
                        scenario_id=f"S{counter:03d}",
                        frequency=frequency,
                        portfolio_size=portfolio_size,
                        slippage_pct=round(float(slippage_pct), 4),
                        round_trip_cost_pct=round(float(round_trip_cost_pct), 4),
                        max_periods=max(int(max_periods), 1),
                    )
                )
                counter += 1
    return scenarios


def run_sensitivity(
    history: pd.DataFrame,
    profiles: list[object],
    scenarios: list[SensitivityScenario],
    *,
    window_periods: int,
    min_fundamental_coverage_pct: float,
    min_profitability_coverage_pct: float,
    min_periods_for_paper: int,
    min_positions_for_paper: int,
    min_pass_rate_pct: float,
    data_status: dict[str, object],
) -> dict[str, object]:
    metric_frames: list[pd.DataFrame] = []
    scenario_rows: list[dict[str, object]] = []
    for scenario in scenarios:
        run = run_research_gate2(
            history,
            profiles,  # type: ignore[arg-type]
            frequency=scenario.frequency,
            portfolio_size=scenario.portfolio_size,
            max_periods=scenario.max_periods,
            window_periods=window_periods,
            round_trip_cost_pct=scenario.round_trip_cost_pct,
            slippage_pct=scenario.slippage_pct,
            min_fundamental_coverage_pct=min_fundamental_coverage_pct,
            min_profitability_coverage_pct=min_profitability_coverage_pct,
            min_periods_for_paper=min_periods_for_paper,
            min_positions_for_paper=min_positions_for_paper,
            data_status=data_status,
        )
        metrics = run["strategy_metrics"].copy()
        for key, value in asdict(scenario).items():
            metrics[key] = value
        metrics["total_cost_pct"] = scenario.round_trip_cost_pct + scenario.slippage_pct
        metric_frames.append(metrics)
        summary = run["summary"]
        scenario_rows.append(
            {
                **asdict(scenario),
                "total_cost_pct": round(scenario.round_trip_cost_pct + scenario.slippage_pct, 4),
                "periods": summary.get("periods", 0),
                "research_gate_pass": bool(summary.get("research_gate_pass", False)),
                "best_strategy": summary.get("best_strategy", ""),
                "best_avg_net_return_pct": summary.get("best_avg_net_return_pct", 0.0),
                "best_avg_excess_return_pct": summary.get("best_avg_excess_return_pct", 0.0),
            }
        )

    scenario_metrics = pd.concat(metric_frames, ignore_index=True) if metric_frames else pd.DataFrame()
    scenario_summary = pd.DataFrame(scenario_rows)
    strategy_robustness = summarize_sensitivity_by_strategy(
        scenario_metrics,
        scenario_count=len(scenarios),
        min_pass_rate_pct=min_pass_rate_pct,
    )
    summary = summarize_sensitivity_gate(strategy_robustness, scenario_summary, data_status=data_status)
    return {
        "summary": summary,
        "scenario_summary": scenario_summary,
        "scenario_metrics": scenario_metrics,
        "strategy_robustness": strategy_robustness,
        "data_status": data_status,
    }


def summarize_sensitivity_by_strategy(
    scenario_metrics: pd.DataFrame,
    *,
    scenario_count: int,
    min_pass_rate_pct: float,
) -> pd.DataFrame:
    if scenario_metrics.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for strategy, frame in scenario_metrics.groupby("strategy_family"):
        paper_mask = frame["promotion_state"].astype(str) == "paper_only"
        paper_count = int(paper_mask.sum())
        pass_rate = round(paper_count / max(scenario_count, 1) * 100.0, 2)
        avg_net = pd.to_numeric(frame["avg_net_return_pct"], errors="coerce")
        avg_excess = pd.to_numeric(frame["avg_excess_return_pct"], errors="coerce")
        drawdown = pd.to_numeric(frame["max_drawdown_pct"], errors="coerce")
        fundamental = pd.to_numeric(frame["fundamental_coverage_pct"], errors="coerce")
        profitability = pd.to_numeric(frame.get("profitability_coverage_pct", pd.Series(dtype=float)), errors="coerce")
        if pass_rate >= min_pass_rate_pct and float(avg_net.min()) > 0 and float(avg_excess.min()) > 0:
            state = "robust_paper_only"
        elif paper_count > 0:
            state = "mixed_paper_only"
        else:
            state = "not_robust"
        rows.append(
            {
                "strategy_family": strategy,
                "scenarios": int(len(frame)),
                "paper_only_scenarios": paper_count,
                "pass_rate_pct": pass_rate,
                "avg_net_return_pct": round(float(avg_net.mean()), 3) if avg_net.notna().any() else 0.0,
                "min_avg_net_return_pct": round(float(avg_net.min()), 3) if avg_net.notna().any() else 0.0,
                "avg_excess_return_pct": round(float(avg_excess.mean()), 3) if avg_excess.notna().any() else 0.0,
                "min_avg_excess_return_pct": round(float(avg_excess.min()), 3) if avg_excess.notna().any() else 0.0,
                "worst_drawdown_pct": round(float(drawdown.min()), 3) if drawdown.notna().any() else 0.0,
                "min_fundamental_coverage_pct": round(float(fundamental.min()), 2) if fundamental.notna().any() else 0.0,
                "min_profitability_coverage_pct": round(float(profitability.min()), 2) if profitability.notna().any() else 0.0,
                "robustness_state": state,
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["robustness_state", "pass_rate_pct", "min_avg_excess_return_pct"],
        ascending=[True, False, False],
    )


def summarize_sensitivity_gate(
    robustness: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    *,
    data_status: dict[str, object],
) -> dict[str, object]:
    robust = robustness[robustness["robustness_state"] == "robust_paper_only"].copy() if not robustness.empty else pd.DataFrame()
    best_strategy = ""
    if not robust.empty:
        robust = robust.sort_values(["pass_rate_pct", "min_avg_excess_return_pct", "avg_excess_return_pct"], ascending=[False, False, False])
        best_strategy = str(robust.iloc[0]["strategy_family"])
    return {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "research_gate": "RG2_SENSITIVITY",
        "sensitivity_pass": bool(not robust.empty),
        "max_allowed_state": "paper_only",
        "live_order_automation": "disabled",
        "scenarios": int(len(scenario_summary)),
        "scenario_pass_rate_pct": round(
            float(pd.Series(scenario_summary.get("research_gate_pass", pd.Series(dtype=bool))).map(bool).sum()) / max(len(scenario_summary), 1) * 100.0,
            2,
        ),
        "strategies": int(robustness["strategy_family"].nunique()) if not robustness.empty else 0,
        "robust_strategies": int(len(robust)),
        "best_robust_strategy": best_strategy,
        "data_status": data_status,
        "note": "Sensitivity validation varies rebalance frequency, portfolio size, and slippage. Passing remains paper_only; live orders stay disabled.",
    }


def write_outputs(
    result: dict[str, object],
    scenarios: list[SensitivityScenario],
    policy: dict[str, object],
    *,
    args: argparse.Namespace,
) -> dict[str, Path]:
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    experiment_dir = args.experiments_dir / f"EXP_{stamp}_RG2_SENSITIVITY"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    args.latest_output.mkdir(parents=True, exist_ok=True)

    summary = result["summary"]
    scenario_summary = result["scenario_summary"]
    scenario_metrics = result["scenario_metrics"]
    strategy_robustness = result["strategy_robustness"]

    config = {
        "args": {
            "frequencies": args.frequencies,
            "portfolio_sizes": args.portfolio_sizes,
            "slippage_pcts": args.slippage_pcts,
            "round_trip_cost_pct": args.round_trip_cost_pct,
            "max_periods": args.max_periods,
            "min_pass_rate_pct": args.min_pass_rate_pct,
            "min_fundamental_coverage_pct": args.min_fundamental_coverage_pct,
            "min_profitability_coverage_pct": args.min_profitability_coverage_pct,
        },
        "policy": policy,
        "scenarios": [asdict(scenario) for scenario in scenarios],
    }
    config_path = experiment_dir / "config.json"
    scenario_summary_path = experiment_dir / "scenario_summary.csv"
    scenario_metrics_path = experiment_dir / "scenario_strategy_metrics.csv"
    robustness_path = experiment_dir / "strategy_robustness.csv"
    json_path = experiment_dir / "metrics.json"
    html_path = experiment_dir / "report.html"

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(scenario_summary, scenario_summary_path)
    write_csv(scenario_metrics, scenario_metrics_path)
    write_csv(strategy_robustness, robustness_path)
    payload = {
        "summary": summary,
        "strategy_robustness": records(strategy_robustness),
        "scenario_summary": records(scenario_summary),
        "data_status": result["data_status"],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(summary, strategy_robustness, scenario_summary, scenario_metrics, experiment_dir)
    html_path.write_text(html, encoding="utf-8-sig")

    (args.latest_output / "latest_summary.json").write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    (args.latest_output / "latest.html").write_text(html, encoding="utf-8-sig")
    write_csv(strategy_robustness, args.latest_output / "latest_strategy_robustness.csv")
    write_csv(scenario_summary, args.latest_output / "latest_scenarios.csv")
    write_csv(scenario_metrics, args.latest_output / "latest_strategy_metrics.csv")
    return {
        "experiment_dir": experiment_dir,
        "config": config_path,
        "scenario_summary": scenario_summary_path,
        "scenario_metrics": scenario_metrics_path,
        "robustness": robustness_path,
        "json": json_path,
        "html": html_path,
    }


def update_registry(path: Path, robustness: pd.DataFrame, experiment_dir: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_at",
        "research_gate",
        "strategy_family",
        "robustness_state",
        "pass_rate_pct",
        "avg_net_return_pct",
        "min_avg_net_return_pct",
        "avg_excess_return_pct",
        "min_avg_excess_return_pct",
        "worst_drawdown_pct",
        "scenarios",
        "paper_only_scenarios",
        "experiment_dir",
    ]
    existing: list[dict[str, object]] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    run_at = str(summary.get("generated_at", datetime.now(tz=KST).isoformat(timespec="seconds")))
    for row in records(robustness):
        existing.append(
            {
                "run_at": run_at,
                "research_gate": "RG2_SENSITIVITY",
                "strategy_family": row.get("strategy_family", ""),
                "robustness_state": row.get("robustness_state", ""),
                "pass_rate_pct": row.get("pass_rate_pct", ""),
                "avg_net_return_pct": row.get("avg_net_return_pct", ""),
                "min_avg_net_return_pct": row.get("min_avg_net_return_pct", ""),
                "avg_excess_return_pct": row.get("avg_excess_return_pct", ""),
                "min_avg_excess_return_pct": row.get("min_avg_excess_return_pct", ""),
                "worst_drawdown_pct": row.get("worst_drawdown_pct", ""),
                "scenarios": row.get("scenarios", ""),
                "paper_only_scenarios": row.get("paper_only_scenarios", ""),
                "experiment_dir": str(experiment_dir),
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)


def render_html(
    summary: dict[str, object],
    robustness: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    scenario_metrics: pd.DataFrame,
    experiment_dir: Path,
) -> str:
    verdict = "ROBUST PAPER ONLY" if summary.get("sensitivity_pass") else "NOT ROBUST ENOUGH"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot RG2 Sensitivity</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .note {{ color: #667085; line-height: 1.6; max-width: 1040px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 22px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; background: #fff; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 22px; font-weight: 700; margin-top: 8px; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 6px 7px; text-align: right; }}
    table.data th:nth-child(1), table.data td:nth-child(1),
    table.data th:nth-child(2), table.data td:nth-child(2),
    table.data th:nth-child(3), table.data td:nth-child(3) {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot RG2 Sensitivity</h1>
  <p class="note">
    RG2 paper_only 결과를 월간/주간 리밸런싱, 포트폴리오 크기, 슬리피지 조건 변화에 대해 재검증합니다.
    이 리포트도 연구 및 paper_only 검토용이며 실전 주문 자동화는 계속 비활성화됩니다.
  </p>
  <div class="grid">
    {metric("Verdict", verdict)}
    {metric("Scenarios", summary.get("scenarios", 0))}
    {metric("Scenario Pass Rate", f"{summary.get('scenario_pass_rate_pct', 0)}%")}
    {metric("Robust Strategies", summary.get("robust_strategies", 0))}
    {metric("Best Robust", summary.get("best_robust_strategy", ""))}
    {metric("Max State", summary.get("max_allowed_state", "paper_only"))}
    {metric("Live Orders", summary.get("live_order_automation", "disabled"))}
    {metric("Generated", summary.get("generated_at", ""))}
  </div>
  <p class="note">Experiment folder: {escape(str(experiment_dir))}</p>
  <h2>Strategy Robustness</h2>
  {table(robustness)}
  <h2>Scenario Summary</h2>
  {table(scenario_summary)}
  <h2>Scenario Strategy Metrics</h2>
  {table(scenario_metrics)}
</body>
</html>"""


def table(frame: pd.DataFrame) -> str:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return '<p class="note">No rows.</p>'
    return frame.to_html(index=False, escape=True, classes="data")


def metric(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def write_csv(frame: object, path: Path) -> None:
    if isinstance(frame, pd.DataFrame):
        frame.to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return frame.to_dict("records") if isinstance(frame, pd.DataFrame) and not frame.empty else []


def parse_text_list(value: str) -> list[str]:
    return [item.strip().lower() for item in str(value).split(",") if item.strip()]


def parse_int_list(value: str) -> list[int]:
    out: list[int] = []
    for item in str(value).split(","):
        try:
            out.append(int(item.strip()))
        except ValueError:
            continue
    return out


def parse_float_list(value: str) -> list[float]:
    out: list[float] = []
    for item in str(value).split(","):
        try:
            out.append(float(item.strip()))
        except ValueError:
            continue
    return out


def unique_text(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text and text not in out:
            out.append(text)
    return out


def unique_int(values: list[int]) -> list[int]:
    out: list[int] = []
    for value in values:
        number = int(value)
        if number not in out:
            out.append(number)
    return out


def unique_float(values: list[float]) -> list[float]:
    out: list[float] = []
    for value in values:
        number = round(float(value), 4)
        if number not in out:
            out.append(number)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
