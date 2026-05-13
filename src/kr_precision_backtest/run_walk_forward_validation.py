from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from html import escape
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .intraday_strategy import evaluate_intraday_day
from .policy import load_policy, policy_to_dict
from .run_historical_intraday_backtest import (
    DEFAULT_METADATA,
    DEFAULT_POLICY,
    DEFAULT_ROOT,
    candidate_passes_policy,
    list_backfilled_files,
)


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "historical_intraday_walk_forward"
NUMERIC_COLUMNS = [
    "rank",
    "signal_score",
    "day_change_pct",
    "market_median_change_pct",
    "value_ratio_20",
    "close_location_pct",
    "lower_tail_recovery_pct",
    "close_vs_open_pct",
    "distance_from_60d_high_pct",
    "entry_gap_pct",
    "net_return_pct",
    "opening_high",
    "opening_low",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run walk-forward validation for KR DayPilot historical intraday strategy.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--candidate-scope", choices=["all", "base-policy"], default="all")
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--holdout-days", type=int, default=20)
    parser.add_argument("--step-days", type=int, default=10)
    parser.add_argument("--min-train-signals", type=int, default=6)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    evaluated = evaluate_backfilled_candidates(args.root, args.metadata, policy, candidate_scope=args.candidate_scope)
    windows, rules = run_walk_forward(
        evaluated,
        train_days=max(args.train_days, 1),
        holdout_days=max(args.holdout_days, 1),
        step_days=max(args.step_days, 1),
        min_train_signals=max(args.min_train_signals, 1),
    )
    summary = summarize_walk_forward(windows, evaluated, rules, policy, args)
    paths = write_outputs(evaluated, windows, rules, summary, args.output)

    print("KR DayPilot walk-forward validation complete.")
    print(f"evaluated_candidates={summary['evaluated_candidates']}, windows={summary['windows']}")
    print(f"holdout_signals={summary['holdout_signals']}, holdout_success_rate={summary['holdout_success_rate']}%")
    print(f"holdout_wilson_low={summary['holdout_wilson_low']}%, research_pass={summary['research_pass']}")
    print(f"HTML={paths['html']}")
    return 0


def evaluate_backfilled_candidates(root: Path, metadata_path: Path, policy: object, *, candidate_scope: str) -> pd.DataFrame:
    rows = []
    for path, meta in list_backfilled_files(root, metadata_path):
        if candidate_scope == "base-policy" and not candidate_passes_policy(meta, policy):
            continue
        bars = pd.read_csv(path, dtype={"ticker": str, "date": str, "time": str})
        result = asdict(evaluate_intraday_day(bars, policy))
        rows.append({**meta, **result, "source_path": str(path)})

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = normalize_results(frame)
    return frame.sort_values(["entry_date_compact", "rank", "ticker"]).reset_index(drop=True)


def normalize_results(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["ticker"] = out["ticker"].astype(str).str.zfill(6)
    out["entry_date_compact"] = out["entry_date"].map(_compact_date)
    out["reference_day_compact"] = out["reference_day"].map(_compact_date)
    out["signal_hhmm"] = out["signal_time"].map(_compact_hhmm)
    for column in NUMERIC_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    out["opening_width_pct"] = 0.0
    valid_opening = (out["opening_high"] > 0) & (out["opening_low"] > 0)
    out.loc[valid_opening, "opening_width_pct"] = (out.loc[valid_opening, "opening_high"] / out.loc[valid_opening, "opening_low"] - 1.0) * 100.0
    out["is_signal"] = out["exit_reason"].astype(str) != "no_signal"
    out["is_success"] = out["exit_reason"].astype(str) == "target_hit"
    out["is_failure_exit"] = out["exit_reason"].astype(str).isin(["stop_loss", "ambiguous_stop_first"])
    out["is_time_exit"] = out["exit_reason"].astype(str) == "time_exit"
    return out


def run_walk_forward(
    evaluated: pd.DataFrame,
    *,
    train_days: int,
    holdout_days: int,
    step_days: int,
    min_train_signals: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if evaluated.empty:
        return pd.DataFrame(), pd.DataFrame()

    rules = build_rule_grid()
    dates = sorted(date for date in evaluated["entry_date_compact"].dropna().astype(str).unique() if len(date) == 8)
    window_rows = []
    rule_score_rows = []
    max_start = len(dates) - train_days - holdout_days
    if max_start < 0:
        return pd.DataFrame(), pd.DataFrame()

    window_no = 1
    for start in range(0, max_start + 1, step_days):
        train_dates = dates[start : start + train_days]
        holdout_dates = dates[start + train_days : start + train_days + holdout_days]
        train = evaluated[evaluated["entry_date_compact"].isin(train_dates)].copy()
        holdout = evaluated[evaluated["entry_date_compact"].isin(holdout_dates)].copy()
        scores = score_rules(train, rules, min_train_signals=min_train_signals)
        scores.insert(0, "window", window_no)
        rule_score_rows.append(scores)
        chosen = choose_rule(scores, rules)
        train_selected = apply_rule(train, chosen)
        holdout_selected = apply_rule(holdout, chosen)
        window_rows.append(
            {
                "window": window_no,
                "train_start": _display_date(train_dates[0]),
                "train_end": _display_date(train_dates[-1]),
                "holdout_start": _display_date(holdout_dates[0]),
                "holdout_end": _display_date(holdout_dates[-1]),
                "chosen_rule": chosen["label"],
                **_prefixed_summary("train", summarize_frame(train_selected)),
                **_prefixed_summary("holdout", summarize_frame(holdout_selected)),
            }
        )
        window_no += 1

    rule_scores = pd.concat(rule_score_rows, ignore_index=True) if rule_score_rows else pd.DataFrame()
    return pd.DataFrame(window_rows), rule_scores


def build_rule_grid() -> list[dict[str, object]]:
    rules = []
    distance_values = [None, -3.0, -5.0, -8.0, -10.0, -12.0, -15.0]
    value_values = [None, 2.5, 3.0, 3.5, 5.0]
    close_location_values = [None, 80.0, 88.0, 90.0]
    signal_cutoffs = [None, 930, 940, 950]
    rank_values = [None, 1, 2]
    for distance in distance_values:
        for value_ratio in value_values:
            for close_location in close_location_values:
                for signal_cutoff in signal_cutoffs:
                    for max_rank in rank_values:
                        conditions = {
                            "distance_max": distance,
                            "value_ratio_max": value_ratio,
                            "close_location_min": close_location,
                            "signal_hhmm_max": signal_cutoff,
                            "rank_max": max_rank,
                        }
                        rules.append({"label": _rule_label(conditions), "conditions": conditions})
    return _dedupe_rules(rules)


def score_rules(train: pd.DataFrame, rules: list[dict[str, object]], *, min_train_signals: int) -> pd.DataFrame:
    rows = []
    for rule in rules:
        selected = apply_rule(train, rule)
        summary = summarize_frame(selected)
        rows.append(
            {
                "rule": rule["label"],
                "eligible": summary["signals"] >= min_train_signals,
                **summary,
            }
        )
    scores = pd.DataFrame(rows)
    if scores.empty:
        return scores
    return scores.sort_values(
        ["eligible", "wilson_low", "success_rate", "signals", "avg_net_return_pct"],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)


def choose_rule(scores: pd.DataFrame, rules: list[dict[str, object]]) -> dict[str, object]:
    if scores.empty:
        return rules[0]
    eligible = scores[scores["eligible"] == True]  # noqa: E712
    row = eligible.iloc[0] if not eligible.empty else scores.iloc[0]
    label = str(row["rule"])
    for rule in rules:
        if rule["label"] == label:
            return rule
    return rules[0]


def apply_rule(frame: pd.DataFrame, rule: dict[str, object]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    conditions = rule["conditions"]
    selected = frame.copy()
    mask = pd.Series(True, index=selected.index)
    if conditions["distance_max"] is not None:
        mask &= selected["distance_from_60d_high_pct"] <= float(conditions["distance_max"])
    if conditions["value_ratio_max"] is not None:
        mask &= selected["value_ratio_20"] <= float(conditions["value_ratio_max"])
    if conditions["close_location_min"] is not None:
        mask &= selected["close_location_pct"] >= float(conditions["close_location_min"])
    if conditions["rank_max"] is not None:
        mask &= selected["rank"] <= int(conditions["rank_max"])
    selected = selected[mask].copy()

    signal_cutoff = conditions["signal_hhmm_max"]
    if signal_cutoff is not None and not selected.empty:
        late_entry = selected["is_signal"] & (selected["signal_hhmm"] > int(signal_cutoff))
        selected.loc[late_entry, "exit_reason"] = "no_signal"
        selected.loc[late_entry, "is_signal"] = False
        selected.loc[late_entry, "is_success"] = False
        selected.loc[late_entry, "is_failure_exit"] = False
        selected.loc[late_entry, "is_time_exit"] = False
        selected.loc[late_entry, "net_return_pct"] = 0.0
    return selected


def summarize_frame(frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {
            "candidates": 0,
            "signals": 0,
            "successes": 0,
            "failure_exits": 0,
            "time_exits": 0,
            "no_signals": 0,
            "success_rate": 0.0,
            "candidate_success_rate": 0.0,
            "wilson_low": 0.0,
            "avg_net_return_pct": 0.0,
        }
    signals = frame[frame["is_signal"]].copy()
    successes = int(frame["is_success"].sum())
    failure_exits = int(frame["is_failure_exit"].sum())
    time_exits = int(frame["is_time_exit"].sum())
    no_signals = int((~frame["is_signal"]).sum())
    wilson_low, _ = wilson(successes, len(signals))
    avg_net = float(signals["net_return_pct"].mean()) if not signals.empty else 0.0
    return {
        "candidates": int(len(frame)),
        "signals": int(len(signals)),
        "successes": successes,
        "failure_exits": failure_exits,
        "time_exits": time_exits,
        "no_signals": no_signals,
        "success_rate": _pct(successes, len(signals)),
        "candidate_success_rate": _pct(successes, len(frame)),
        "wilson_low": round(wilson_low, 2),
        "avg_net_return_pct": round(avg_net, 3),
    }


def summarize_walk_forward(
    windows: pd.DataFrame,
    evaluated: pd.DataFrame,
    rules: pd.DataFrame,
    policy: object,
    args: argparse.Namespace,
) -> dict[str, object]:
    policy_json = json.dumps(policy_to_dict(policy), sort_keys=True, ensure_ascii=False)
    if windows.empty:
        return {
            "generated_at": datetime.now(tz=KST).isoformat(),
            "policy_hash": hashlib.sha256(policy_json.encode("utf-8")).hexdigest()[:12],
            "candidate_scope": args.candidate_scope,
            "evaluated_candidates": int(len(evaluated)),
            "windows": 0,
            "holdout_signals": 0,
            "holdout_successes": 0,
            "holdout_failures": 0,
            "holdout_time_exits": 0,
            "holdout_success_rate": 0.0,
            "holdout_candidate_success_rate": 0.0,
            "holdout_wilson_low": 0.0,
            "holdout_avg_net_return_pct": 0.0,
            "research_pass": False,
            "research_note": "not enough chronological data for requested walk-forward windows",
        }

    holdout_signals = int(windows["holdout_signals"].sum())
    holdout_successes = int(windows["holdout_successes"].sum())
    holdout_candidates = int(windows["holdout_candidates"].sum())
    holdout_failures = int(windows["holdout_failure_exits"].sum())
    holdout_time_exits = int(windows["holdout_time_exits"].sum())
    wilson_low, _ = wilson(holdout_successes, holdout_signals)
    avg_net = _weighted_average(windows, "holdout_avg_net_return_pct", "holdout_signals")
    research_pass = bool(
        holdout_signals >= policy.oos_min_trades
        and _pct(holdout_successes, holdout_signals) >= policy.research_pass_success_rate_pct
        and wilson_low >= policy.research_wilson_lower_pct
    )
    rule_grid_size = int(len(rules["rule"].unique())) if not rules.empty else 0
    holdout_success_rate = _pct(holdout_successes, holdout_signals)
    holdout_candidate_success_rate = _pct(holdout_successes, holdout_candidates)
    warnings = []
    if rule_grid_size > max(int(len(evaluated)), 1):
        warnings.append("rule grid is larger than evaluated candidates; treat selected filters as exploratory")
    if holdout_signals < 30:
        warnings.append("holdout signals are below 30; success rate is unstable")
    if args.min_train_signals < 10:
        warnings.append("min train signals is below 10; filter selection can overfit")

    return {
        "generated_at": datetime.now(tz=KST).isoformat(),
        "policy_hash": hashlib.sha256(policy_json.encode("utf-8")).hexdigest()[:12],
        "candidate_scope": args.candidate_scope,
        "train_days": args.train_days,
        "holdout_days": args.holdout_days,
        "step_days": args.step_days,
        "min_train_signals": args.min_train_signals,
        "evaluated_candidates": int(len(evaluated)),
        "rule_grid_size": rule_grid_size,
        "windows": int(len(windows)),
        "holdout_candidates": holdout_candidates,
        "holdout_signals": holdout_signals,
        "holdout_successes": holdout_successes,
        "holdout_failures": holdout_failures,
        "holdout_time_exits": holdout_time_exits,
        "holdout_success_rate": holdout_success_rate,
        "holdout_candidate_success_rate": holdout_candidate_success_rate,
        "holdout_wilson_low": round(wilson_low, 2),
        "holdout_avg_net_return_pct": round(avg_net, 3),
        "required_oos_min_trades": policy.oos_min_trades,
        "required_success_rate_pct": policy.research_pass_success_rate_pct,
        "required_wilson_lower_pct": policy.research_wilson_lower_pct,
        "research_pass": research_pass,
        "research_note": "pass requires enough holdout signals, success rate, and Wilson lower bound",
        "warnings": warnings,
    }


def write_outputs(
    evaluated: pd.DataFrame,
    windows: pd.DataFrame,
    rules: pd.DataFrame,
    summary: dict[str, object],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    evaluated_csv = output_dir / f"walk_forward_evaluated_{stamp}.csv"
    windows_csv = output_dir / f"walk_forward_windows_{stamp}.csv"
    rules_csv = output_dir / f"walk_forward_rule_scores_{stamp}.csv"
    json_path = output_dir / f"walk_forward_summary_{stamp}.json"
    html_path = output_dir / f"walk_forward_{stamp}.html"
    latest_html = output_dir / "latest.html"
    latest_json = output_dir / "latest_summary.json"
    latest_windows = output_dir / "latest_windows.csv"

    evaluated.to_csv(evaluated_csv, index=False, encoding="utf-8-sig")
    windows.to_csv(windows_csv, index=False, encoding="utf-8-sig")
    rules.to_csv(rules_csv, index=False, encoding="utf-8-sig")
    windows.to_csv(latest_windows, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps({"summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps({"summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(windows, rules, summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"html": html_path, "json": json_path, "windows_csv": windows_csv, "rules_csv": rules_csv}


def render_html(windows: pd.DataFrame, rules: pd.DataFrame, summary: dict[str, object]) -> str:
    windows_table = windows.to_html(index=False, escape=True) if not windows.empty else "<p>walk-forward window가 없습니다.</p>"
    top_rules = rules.head(50).to_html(index=False, escape=True) if not rules.empty else "<p>rule score가 없습니다.</p>"
    verdict = "통과" if summary.get("research_pass") else "미통과"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot Walk-Forward 검증</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #20242d; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d0d5dd; border-radius: 8px; padding: 14px 16px; }}
    .label {{ color: #667085; font-size: 13px; }}
    .value {{ font-size: 26px; font-weight: 700; margin-top: 8px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #eaecf0; padding: 6px 7px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
  </style>
</head>
<body>
  <h1>KR DayPilot Walk-Forward 검증</h1>
  <p>과거 데이터에서 고른 조건이 뒤쪽 기간에서도 유지되는지 확인하는 과최적화 방지 검증입니다.</p>
  <div class="grid">
    {card("판정", verdict)}
    {card("평가 후보", summary.get("evaluated_candidates", 0))}
    {card("Holdout 진입", summary.get("holdout_signals", 0))}
    {card("Holdout 성공률", str(summary.get("holdout_success_rate", 0)) + "%")}
    {card("Wilson 하한", str(summary.get("holdout_wilson_low", 0)) + "%")}
    {card("후보 기준 성공률", str(summary.get("holdout_candidate_success_rate", 0)) + "%")}
    {card("평균 순수익률", str(summary.get("holdout_avg_net_return_pct", 0)) + "%")}
    {card("윈도우", summary.get("windows", 0))}
  </div>
  <h2>검증 요약</h2>
  <pre>{escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
  <h2>Walk-Forward 윈도우</h2>
  {windows_table}
  <h2>훈련 구간 Rule Score 상위 50개</h2>
  {top_rules}
</body>
</html>"""


def card(label: str, value: object) -> str:
    return f'<div class="card"><div class="label">{escape(str(label))}</div><div class="value">{escape(str(value))}</div></div>'


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.96
    p = successes / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    margin = z * ((p * (1 - p) + z**2 / (4 * total)) / total) ** 0.5 / denom
    return max((center - margin) * 100.0, 0.0), min((center + margin) * 100.0, 100.0)


def _prefixed_summary(prefix: str, summary: dict[str, object]) -> dict[str, object]:
    return {f"{prefix}_{key}": value for key, value in summary.items()}


def _weighted_average(frame: pd.DataFrame, value_column: str, weight_column: str) -> float:
    if frame.empty or frame[weight_column].sum() <= 0:
        return 0.0
    return float((frame[value_column] * frame[weight_column]).sum() / frame[weight_column].sum())


def _rule_label(conditions: dict[str, object]) -> str:
    parts = []
    if conditions["distance_max"] is not None:
        parts.append(f"distance<={conditions['distance_max']:g}")
    if conditions["value_ratio_max"] is not None:
        parts.append(f"value_ratio<={conditions['value_ratio_max']:g}")
    if conditions["close_location_min"] is not None:
        parts.append(f"close_location>={conditions['close_location_min']:g}")
    if conditions["signal_hhmm_max"] is not None:
        parts.append(f"signal<={conditions['signal_hhmm_max']}")
    if conditions["rank_max"] is not None:
        parts.append(f"rank<={conditions['rank_max']}")
    return "; ".join(parts) if parts else "base"


def _dedupe_rules(rules: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    seen = set()
    for rule in rules:
        label = str(rule["label"])
        if label in seen:
            continue
        seen.add(label)
        result.append(rule)
    return result


def _compact_date(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    return text[:8] if len(text) >= 8 else ""


def _display_date(value: object) -> str:
    text = _compact_date(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else str(value)


def _compact_hhmm(value: object) -> int:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) < 4:
        return 9999
    return int(text[:4])


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100.0, 2) if denominator else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
