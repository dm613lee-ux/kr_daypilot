from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Policy:
    mode: str
    take_profit_pct: float
    stop_loss_pct: float
    hard_stop_loss_pct: float
    buy_start: str
    buy_end: str
    max_display_candidates: int
    max_order_candidates: int
    max_position_value_krw: float
    max_daily_loss_krw: float
    planned_loss_pct: float
    swing_take_profit_pct: float
    swing_stop_loss_pct: float
    swing_entry_discount_pct: float
    swing_min_open_gap_pct: float
    swing_max_open_gap_pct: float
    swing_max_down_gap_pct: float
    swing_hold_days: int
    min_avg_trading_value_20d_krw: float
    min_intraday_trading_value_0930_krw: float
    opening_range_min_pct: float
    opening_range_max_pct: float
    vwap_reclaim_opening_high_tolerance_pct: float
    vwap_reclaim_volume_multiplier: float
    vwap_reclaim_pullback_lookback_bars: int
    min_reference_day_change_pct: float
    min_signal_vwap_premium_pct: float
    min_reliable_close_location_pct: float
    max_reliable_value_ratio_20: float
    max_reliable_distance_from_60d_high_pct: float
    max_spread_pct: float
    backtest_round_trip_cost_default_pct: float
    oos_min_trades: int
    research_pass_success_rate_pct: float
    live_candidate_success_rate_pct: float
    research_wilson_lower_pct: float
    live_wilson_lower_pct: float


def load_policy(path: Path) -> Policy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Policy(
        mode=str(raw.get("mode", "intraday_high_precision")),
        take_profit_pct=float(raw.get("take_profit_pct", 1.8)),
        stop_loss_pct=float(raw.get("stop_loss_pct", 0.9)),
        hard_stop_loss_pct=float(raw.get("hard_stop_loss_pct", 1.2)),
        buy_start=str(raw.get("buy_start", "09:10")),
        buy_end=str(raw.get("buy_end", "10:30")),
        max_display_candidates=int(raw.get("max_display_candidates", 5)),
        max_order_candidates=int(raw.get("max_order_candidates", 2)),
        max_position_value_krw=float(raw.get("max_position_value_krw", 300_000)),
        max_daily_loss_krw=float(raw.get("max_daily_loss_krw", 30_000)),
        planned_loss_pct=float(raw.get("planned_loss_pct", raw.get("stop_loss_pct", 0.9))),
        swing_take_profit_pct=float(raw.get("swing_take_profit_pct", 3.0)),
        swing_stop_loss_pct=float(raw.get("swing_stop_loss_pct", 2.0)),
        swing_entry_discount_pct=float(raw.get("swing_entry_discount_pct", 0.5)),
        swing_min_open_gap_pct=float(raw.get("swing_min_open_gap_pct", 0.5)),
        swing_max_open_gap_pct=float(raw.get("swing_max_open_gap_pct", 4.0)),
        swing_max_down_gap_pct=float(raw.get("swing_max_down_gap_pct", -4.0)),
        swing_hold_days=int(raw.get("swing_hold_days", 3)),
        min_avg_trading_value_20d_krw=float(raw.get("min_avg_trading_value_20d_krw", 10_000_000_000)),
        min_intraday_trading_value_0930_krw=float(raw.get("min_intraday_trading_value_0930_krw", 2_000_000_000)),
        opening_range_min_pct=float(raw.get("opening_range_min_pct", 0.4)),
        opening_range_max_pct=float(raw.get("opening_range_max_pct", 4.5)),
        vwap_reclaim_opening_high_tolerance_pct=float(raw.get("vwap_reclaim_opening_high_tolerance_pct", 1.0)),
        vwap_reclaim_volume_multiplier=float(raw.get("vwap_reclaim_volume_multiplier", 1.0)),
        vwap_reclaim_pullback_lookback_bars=int(raw.get("vwap_reclaim_pullback_lookback_bars", 5)),
        min_reference_day_change_pct=float(raw.get("min_reference_day_change_pct", 5.0)),
        min_signal_vwap_premium_pct=float(raw.get("min_signal_vwap_premium_pct", 0.5)),
        min_reliable_close_location_pct=float(raw.get("min_reliable_close_location_pct", 90.0)),
        max_reliable_value_ratio_20=float(raw.get("max_reliable_value_ratio_20", 2.5)),
        max_reliable_distance_from_60d_high_pct=float(raw.get("max_reliable_distance_from_60d_high_pct", -5.0)),
        max_spread_pct=float(raw.get("max_spread_pct", 0.3)),
        backtest_round_trip_cost_default_pct=float(raw.get("backtest_round_trip_cost_default_pct", 0.6)),
        oos_min_trades=int(raw.get("oos_min_trades", 300)),
        research_pass_success_rate_pct=float(raw.get("research_pass_success_rate_pct", 62)),
        live_candidate_success_rate_pct=float(raw.get("live_candidate_success_rate_pct", 70)),
        research_wilson_lower_pct=float(raw.get("research_wilson_lower_pct", 55)),
        live_wilson_lower_pct=float(raw.get("live_wilson_lower_pct", 60)),
    )


def policy_to_dict(policy: Policy) -> dict[str, Any]:
    return {
        "mode": policy.mode,
        "take_profit_pct": policy.take_profit_pct,
        "stop_loss_pct": policy.stop_loss_pct,
        "hard_stop_loss_pct": policy.hard_stop_loss_pct,
        "buy_start": policy.buy_start,
        "buy_end": policy.buy_end,
        "max_order_candidates": policy.max_order_candidates,
        "max_position_value_krw": policy.max_position_value_krw,
        "max_daily_loss_krw": policy.max_daily_loss_krw,
        "planned_loss_pct": policy.planned_loss_pct,
        "swing_take_profit_pct": policy.swing_take_profit_pct,
        "swing_stop_loss_pct": policy.swing_stop_loss_pct,
        "swing_entry_discount_pct": policy.swing_entry_discount_pct,
        "swing_min_open_gap_pct": policy.swing_min_open_gap_pct,
        "swing_max_open_gap_pct": policy.swing_max_open_gap_pct,
        "swing_max_down_gap_pct": policy.swing_max_down_gap_pct,
        "swing_hold_days": policy.swing_hold_days,
        "min_avg_trading_value_20d_krw": policy.min_avg_trading_value_20d_krw,
        "opening_range_min_pct": policy.opening_range_min_pct,
        "opening_range_max_pct": policy.opening_range_max_pct,
        "vwap_reclaim_opening_high_tolerance_pct": policy.vwap_reclaim_opening_high_tolerance_pct,
        "vwap_reclaim_volume_multiplier": policy.vwap_reclaim_volume_multiplier,
        "vwap_reclaim_pullback_lookback_bars": policy.vwap_reclaim_pullback_lookback_bars,
        "min_reference_day_change_pct": policy.min_reference_day_change_pct,
        "min_signal_vwap_premium_pct": policy.min_signal_vwap_premium_pct,
        "min_reliable_close_location_pct": policy.min_reliable_close_location_pct,
        "max_reliable_value_ratio_20": policy.max_reliable_value_ratio_20,
        "max_reliable_distance_from_60d_high_pct": policy.max_reliable_distance_from_60d_high_pct,
        "backtest_round_trip_cost_default_pct": policy.backtest_round_trip_cost_default_pct,
        "oos_min_trades": policy.oos_min_trades,
        "research_pass_success_rate_pct": policy.research_pass_success_rate_pct,
        "research_wilson_lower_pct": policy.research_wilson_lower_pct,
    }
