from __future__ import annotations

import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .data import add_daily_proxy_features, load_price_history
from .eod_context import add_eod_context_features
from .policy import load_policy, policy_to_dict
from .swing_strategy import add_swing_features, build_paper_order, score_swing_candidates, select_swing_candidates


KST = ZoneInfo("Asia/Seoul")
PROGRAM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRICE_HISTORY = PROGRAM_ROOT / "data" / "kr_stock_price_history.csv"
DEFAULT_POLICY = PROGRAM_ROOT / "config" / "policy.defaults.json"
DEFAULT_RISK_CONTEXT = PROGRAM_ROOT / "output" / "risk_context" / "latest.csv"
DEFAULT_OUTPUT = PROGRAM_ROOT / "output" / "app"
DEFAULT_EOD_CONTEXT = PROGRAM_ROOT / "data" / "eod_context"
DEFAULT_UNIVERSE = PROGRAM_ROOT / "data" / "kr_universe.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate KR DayPilot paper order plan app.")
    parser.add_argument("--price-history", type=Path, default=DEFAULT_PRICE_HISTORY)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--risk-context", type=Path, default=DEFAULT_RISK_CONTEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--eod-context-dir", type=Path, default=DEFAULT_EOD_CONTEXT)
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    history = add_swing_features(add_daily_proxy_features(load_price_history(args.price_history)))
    history = add_eod_context_features(history, context_dir=args.eod_context_dir, universe_path=args.universe)
    plans, excluded, summary = build_latest_plan(history, policy, risk_context_path=args.risk_context)
    paths = write_outputs(plans, excluded, summary, policy, args.output)

    print("KR DayPilot paper order plan complete.")
    print(f"Status: {summary['today_status']}")
    print(f"Plans: {summary['plan_count']}")
    print(f"HTML: {paths['html']}")
    return 0


def build_latest_plan(
    history: pd.DataFrame,
    policy: object,
    *,
    risk_context_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    latest_day = str(history["source_bas_dt"].astype(str).max())
    day_rows = history[history["source_bas_dt"].astype(str) == latest_day].copy()
    scored = score_swing_candidates(day_rows, policy)
    candidates = select_swing_candidates(day_rows, policy, max_candidates=policy.max_display_candidates)
    context = load_risk_context(risk_context_path)
    plans = []
    for rank, candidate in enumerate(candidates.head(policy.max_order_candidates).to_dict("records"), start=1):
        plan = build_paper_order(candidate, policy, rank=rank)
        plan.update(execution_gate(plan, context.get(str(plan["ticker"]).zfill(6), {}), policy))
        plans.append(plan)

    plan_frame = pd.DataFrame(plans)
    blocked = scored[scored["candidate_status"] != "pass"].copy()
    if not blocked.empty:
        blocked = blocked.sort_values("alpha_score", ascending=False).head(40)
        blocked = blocked[
            [
                "source_bas_dt",
                "ticker",
                "company",
                "market",
                "alpha_score",
                "block_reason",
                "ret_5d_pct",
                "value_ratio_20",
                "close_location_pct",
            ]
        ].copy()
    ready_count = int((plan_frame.get("execution_gate_status", pd.Series(dtype=str)) == "pass").sum()) if not plan_frame.empty else 0
    pending_count = int((plan_frame.get("execution_gate_status", pd.Series(dtype=str)) == "pending_live_context").sum()) if not plan_frame.empty else 0
    if plan_frame.empty:
        status = "거래 없음"
    elif pending_count:
        status = "데이터 부족"
    elif ready_count:
        status = "거래 가능"
    else:
        status = "위험일"

    summary = {
        "generated_at": datetime.now(tz=KST).isoformat(timespec="seconds"),
        "today_status": status,
        "reference_day": _display_day(latest_day),
        "candidate_count": int(len(candidates)),
        "plan_count": int(len(plan_frame)),
        "ready_count": ready_count,
        "pending_count": pending_count,
        "blocked_context_count": int(len(plan_frame) - ready_count - pending_count) if not plan_frame.empty else 0,
        "risk_context_path": str(risk_context_path) if risk_context_path.exists() else "",
        "message": status_message(status),
    }
    return plan_frame, blocked, summary


def execution_gate(plan: dict[str, object], context: dict[str, object], policy: object) -> dict[str, object]:
    if not context:
        return {
            "execution_gate_status": "pending_live_context",
            "execution_gate_reason": "장전/장초반 현재가·호가·체결강도 데이터 대기",
            "current_price": 0.0,
            "spread_pct": 0.0,
            "trade_strength": 0.0,
            "index_change_pct": 0.0,
        }
    reasons = []
    spread = _float(context.get("spread_pct"))
    trade_strength = _float(context.get("trade_strength"))
    index_change = _float(context.get("index_change_pct"))
    current_price = _float(context.get("current_price"))
    reference_close = _float(plan.get("reference_close"))
    if spread > float(getattr(policy, "max_spread_pct", 0.3)):
        reasons.append("spread_too_wide")
    if index_change <= -1.5:
        reasons.append("market_index_weak")
    if current_price > 0 and reference_close > 0:
        live_gap = (current_price / reference_close - 1.0) * 100.0
        if live_gap > float(getattr(policy, "swing_max_open_gap_pct", 4.0)):
            reasons.append("live_gap_up_chase_risk")
        if live_gap < float(getattr(policy, "swing_max_down_gap_pct", -4.0)):
            reasons.append("live_gap_down_risk")
        if live_gap < float(getattr(policy, "swing_min_open_gap_pct", 0.5)):
            reasons.append("morning_confirmation_missing")
    status = "blocked" if reasons else "pass"
    return {
        "execution_gate_status": status,
        "execution_gate_reason": ";".join(reasons),
        "current_price": round(current_price, 2),
        "spread_pct": round(spread, 3),
        "trade_strength": round(trade_strength, 3),
        "index_change_pct": round(index_change, 3),
    }


def load_risk_context(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype={"ticker": str})
    if frame.empty or "ticker" not in frame.columns:
        return {}
    return {str(row.get("ticker", "")).zfill(6): row.to_dict() for _, row in frame.iterrows()}


def write_outputs(
    plans: pd.DataFrame,
    excluded: pd.DataFrame,
    summary: dict[str, object],
    policy: object,
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"paper_plan_{stamp}.csv"
    excluded_csv = output_dir / f"paper_plan_excluded_{stamp}.csv"
    json_path = output_dir / f"paper_plan_{stamp}.json"
    html_path = output_dir / f"paper_plan_{stamp}.html"
    latest_csv = output_dir / "latest.csv"
    latest_excluded_csv = output_dir / "latest_excluded.csv"
    latest_json = output_dir / "latest.json"
    latest_html = output_dir / "latest.html"
    plans.to_csv(csv_path, index=False, encoding="utf-8-sig")
    plans.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    excluded.to_csv(excluded_csv, index=False, encoding="utf-8-sig")
    excluded.to_csv(latest_excluded_csv, index=False, encoding="utf-8-sig")
    payload = {"summary": summary, "policy": policy_to_dict(policy)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(plans, excluded, summary)
    html_path.write_text(html, encoding="utf-8-sig")
    latest_html.write_text(html, encoding="utf-8-sig")
    return {"html": html_path, "json": json_path, "csv": csv_path}


def render_html(plans: pd.DataFrame, excluded: pd.DataFrame, summary: dict[str, object]) -> str:
    cards = "\n".join(order_card(row) for _, row in plans.iterrows()) if not plans.empty else '<section class="empty">오늘 생성된 페이퍼 주문 플랜이 없습니다.</section>'
    excluded_table = excluded.to_html(index=False, escape=True, classes="data") if not excluded.empty else "<p>제외 후보가 없습니다.</p>"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>KR DayPilot 오늘의 페이퍼 주문 플랜</title>
  <style>
    :root {{ --text: #20242d; --muted: #667085; --line: #d0d5dd; --panel: #f8fafc; --good: #067647; --warn: #b54708; --bad: #b42318; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: var(--text); background: #ffffff; }}
    header {{ padding: 28px 36px 18px; border-bottom: 1px solid var(--line); }}
    main {{ padding: 24px 36px 40px; max-width: 1280px; }}
    .status {{ display: inline-flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 8px; background: var(--panel); border: 1px solid var(--line); font-weight: 700; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin: 22px 0; }}
    .card {{ border: 1px solid var(--line); border-radius: 8px; padding: 18px; background: #fff; }}
    .card h2 {{ margin: 0 0 10px; font-size: 22px; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: var(--panel); color: var(--muted); font-size: 13px; }}
    .pass {{ color: var(--good); }}
    .blocked {{ color: var(--bad); }}
    .pending {{ color: var(--warn); }}
    .prices {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 14px 0; }}
    .price {{ background: var(--panel); border: 1px solid #eaecf0; border-radius: 8px; padding: 10px; }}
    .label {{ color: var(--muted); font-size: 12px; }}
    .value {{ font-size: 20px; font-weight: 800; margin-top: 4px; }}
    table.data {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 12px; }}
    table.data th, table.data td {{ border-bottom: 1px solid #eaecf0; padding: 7px 8px; text-align: right; }}
    table.data th:nth-child(2), table.data td:nth-child(2),
    table.data th:nth-child(3), table.data td:nth-child(3) {{ text-align: left; }}
    .empty {{ border: 1px solid var(--line); border-radius: 8px; padding: 18px; background: var(--panel); }}
  </style>
</head>
<body>
  <header>
    <div class="status">{escape(str(summary["today_status"]))}</div>
    <h1>오늘의 페이퍼 주문 플랜</h1>
    <p class="muted">{escape(str(summary["message"]))}</p>
    <p class="muted">기준일 {escape(str(summary["reference_day"]))} · 생성 {escape(str(summary["generated_at"]))}</p>
  </header>
  <main>
    <section class="grid">
      {metric("후보", summary.get("candidate_count", 0))}
      {metric("플랜", summary.get("plan_count", 0))}
      {metric("실행 가능", summary.get("ready_count", 0))}
      {metric("데이터 대기", summary.get("pending_count", 0))}
    </section>
    <section class="grid">{cards}</section>
    <h2>제외된 후보</h2>
    {excluded_table}
  </main>
</body>
</html>"""


def order_card(row: pd.Series) -> str:
    status = str(row.get("execution_gate_status", ""))
    klass = "pass" if status == "pass" else ("pending" if status == "pending_live_context" else "blocked")
    return f"""<article class="card">
  <span class="badge {klass}">{escape(status_label(status))}</span>
  <h2>{escape(str(row.get("paper_order_rank", "")))}순위 · {escape(str(row.get("ticker", "")))} · {escape(str(row.get("company", "")))}</h2>
  <p class="muted">{escape(str(row.get("feature_summary", "")))}</p>
  <div class="prices">
    {price_box("매수가", row.get("entry_limit_price", 0))}
    {price_box("목표가", row.get("target_price", 0))}
    {price_box("손절가", row.get("stop_price", 0))}
  </div>
  <p>수량 <strong>{escape(str(row.get("quantity", 0)))}</strong>주 · 예상 손실 <strong>{format_krw(row.get("planned_max_loss_krw", 0))}</strong> · 취소 {escape(str(row.get("cancel_time", "")))}</p>
  <p class="muted">게이트: {escape(str(row.get("execution_gate_reason", "")) or "통과")} · 보유 {escape(str(row.get("holding_period", "")))}</p>
</article>"""


def price_box(label: str, value: object) -> str:
    return f'<div class="price"><div class="label">{escape(label)}</div><div class="value">{format_krw(value)}</div></div>'


def metric(label: str, value: object) -> str:
    return f'<article class="card"><div class="label">{escape(label)}</div><div class="value">{escape(str(value))}</div></article>'


def status_label(status: str) -> str:
    return {"pass": "실행 가능", "pending_live_context": "데이터 대기", "blocked": "보류"}.get(status, status)


def status_message(status: str) -> str:
    return {
        "거래 가능": "페이퍼 주문 플랜이 실행 조건을 통과했습니다. 실전 주문이 아니라 검증용입니다.",
        "거래 없음": "오늘은 기준을 통과한 페이퍼 주문 후보가 없습니다.",
        "위험일": "후보는 있으나 실행 게이트에서 보류되었습니다.",
        "데이터 부족": "후보는 있으나 장전/장초반 현재가·호가·체결강도 데이터가 부족합니다.",
    }.get(status, "")


def format_krw(value: object) -> str:
    number = _float(value)
    if number <= 0:
        return "-"
    return f"{number:,.0f}원"


def _float(value: object) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _display_day(value: object) -> str:
    text = "".join(ch for ch in str(value) if ch.isdigit())
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
