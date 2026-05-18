from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.rg2_factor_engine import (
    FactorConfig,
    add_rg2_price_factors,
    build_rebalance_signal_days,
    merge_fundamental_snapshots,
    score_rg2_candidates,
    select_rg2_portfolio,
)
from kr_precision_backtest.run_research_gate2 import promotion_state


class Rg2FactorEngineTest(unittest.TestCase):
    def test_price_factors_include_shared_eod_context_columns(self) -> None:
        rows = []
        for day in range(1, 122):
            rows.append(
                {
                    "source_bas_dt": f"2026{((day - 1) // 28) + 1:02d}{((day - 1) % 28) + 1:02d}",
                    "ticker": "000001",
                    "close": float(100 + day),
                    "trading_value": 10_000_000_000.0,
                }
            )

        factors = add_rg2_price_factors(pd.DataFrame(rows))

        self.assertIn("ret_5d_pct", factors.columns)
        self.assertIn("ret_60d_pct", factors.columns)
        self.assertIn("volatility_60d_pct", factors.columns)
        self.assertTrue(factors["ret_5d_pct"].notna().any())

    def test_multifactor_score_prefers_balanced_value_quality_momentum_low_vol(self) -> None:
        day_rows = pd.DataFrame(
            [
                {
                    "source_bas_dt": "20260331",
                    "ticker": "000001",
                    "company": "A",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 20_000_000_000,
                    "earnings_yield": 0.12,
                    "book_to_market": 0.9,
                    "sales_yield": 1.4,
                    "operating_margin": 0.18,
                    "roe": 0.16,
                    "roa": 0.08,
                    "ret_60d_pct": 18.0,
                    "ret_120d_pct": 26.0,
                    "volatility_60d_pct": 1.3,
                    "disclosure_risk_flag": False,
                },
                {
                    "source_bas_dt": "20260331",
                    "ticker": "000002",
                    "company": "B",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 20_000_000_000,
                    "earnings_yield": 0.03,
                    "book_to_market": 0.2,
                    "sales_yield": 0.4,
                    "operating_margin": 0.04,
                    "roe": 0.03,
                    "roa": 0.01,
                    "ret_60d_pct": 2.0,
                    "ret_120d_pct": 3.0,
                    "volatility_60d_pct": 4.2,
                    "disclosure_risk_flag": False,
                },
            ]
        )

        selected = select_rg2_portfolio(day_rows, FactorConfig(), portfolio_size=1)

        self.assertEqual(selected.iloc[0]["ticker"], "000001")
        self.assertGreater(selected.iloc[0]["rg2_composite_score"], 80.0)
        self.assertEqual(selected.iloc[0]["candidate_status"], "pass")

    def test_disclosure_risk_is_hard_blocked_by_default(self) -> None:
        day_rows = pd.DataFrame(
            [
                {
                    "source_bas_dt": "20260331",
                    "ticker": "000001",
                    "company": "A",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 20_000_000_000,
                    "earnings_yield": 0.12,
                    "operating_margin": 0.18,
                    "ret_60d_pct": 18.0,
                    "ret_120d_pct": 26.0,
                    "volatility_60d_pct": 1.3,
                    "disclosure_risk_flag": True,
                },
                {
                    "source_bas_dt": "20260331",
                    "ticker": "000002",
                    "company": "B",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 20_000_000_000,
                    "earnings_yield": 0.08,
                    "operating_margin": 0.12,
                    "ret_60d_pct": 10.0,
                    "ret_120d_pct": 12.0,
                    "volatility_60d_pct": 2.1,
                    "disclosure_risk_flag": False,
                },
            ]
        )

        scored = score_rg2_candidates(day_rows, FactorConfig())
        selected = select_rg2_portfolio(day_rows, FactorConfig(), portfolio_size=2)

        blocked = scored[scored["ticker"] == "000001"].iloc[0]
        self.assertEqual(blocked["candidate_status"], "blocked")
        self.assertIn("disclosure_risk", blocked["block_reason"])
        self.assertEqual(selected["ticker"].tolist(), ["000002"])

    def test_rebalance_signal_days_use_last_trading_day_per_period(self) -> None:
        days = ["20260129", "20260130", "20260202", "20260227", "20260302"]

        monthly = build_rebalance_signal_days(days, "monthly")
        weekly = build_rebalance_signal_days(days, "weekly")

        self.assertEqual(monthly, ["20260130", "20260227", "20260302"])
        self.assertEqual(weekly, ["20260130", "20260202", "20260227", "20260302"])

    def test_fundamental_snapshots_are_point_in_time_forward_filled(self) -> None:
        history = pd.DataFrame(
            [
                {"source_bas_dt": "20260110", "ticker": "000001", "market_cap": 100.0},
                {"source_bas_dt": "20260210", "ticker": "000001", "market_cap": 100.0},
                {"source_bas_dt": "20260210", "ticker": "000002", "market_cap": 100.0},
            ]
        )
        fundamentals = pd.DataFrame(
            [
                {
                    "source_bas_dt": "20260201",
                    "ticker": "000001",
                    "revenue": 200.0,
                    "operating_income": 20.0,
                    "net_income": 10.0,
                    "equity": 50.0,
                    "total_assets": 120.0,
                }
            ]
        )

        merged = merge_fundamental_snapshots(history, fundamentals)

        before = merged[(merged["ticker"] == "000001") & (merged["source_bas_dt"] == "20260110")].iloc[0]
        after = merged[(merged["ticker"] == "000001") & (merged["source_bas_dt"] == "20260210")].iloc[0]
        missing = merged[(merged["ticker"] == "000002") & (merged["source_bas_dt"] == "20260210")].iloc[0]
        self.assertFalse(bool(before["fundamental_available"]))
        self.assertTrue(bool(after["fundamental_available"]))
        self.assertAlmostEqual(after["earnings_yield"], 0.10)
        self.assertAlmostEqual(after["operating_margin"], 0.10)
        self.assertFalse(bool(missing["fundamental_available"]))

    def test_promotion_requires_fundamental_coverage_and_caps_at_paper_only(self) -> None:
        metrics = {
            "periods": 12,
            "selected_positions": 120,
            "fundamental_coverage_pct": 0.0,
            "avg_net_return_pct": 3.0,
            "avg_excess_return_pct": 1.0,
            "positive_period_rate_pct": 70.0,
            "max_drawdown_pct": -5.0,
        }

        state, _ = promotion_state(
            metrics,
            min_fundamental_coverage_pct=50.0,
            min_profitability_coverage_pct=50.0,
            min_periods_for_paper=12,
            min_positions_for_paper=80,
        )
        metrics["fundamental_coverage_pct"] = 100.0
        metrics["profitability_coverage_pct"] = 100.0
        paper_state, note = promotion_state(
            metrics,
            min_fundamental_coverage_pct=50.0,
            min_profitability_coverage_pct=50.0,
            min_periods_for_paper=12,
            min_positions_for_paper=80,
        )

        self.assertEqual(state, "needs_fundamental_data")
        self.assertEqual(paper_state, "paper_only")
        self.assertIn("paper-only", note)

    def test_promotion_requires_profitability_coverage(self) -> None:
        metrics = {
            "periods": 12,
            "selected_positions": 120,
            "fundamental_coverage_pct": 100.0,
            "profitability_coverage_pct": 0.0,
            "avg_net_return_pct": 3.0,
            "avg_excess_return_pct": 1.0,
            "positive_period_rate_pct": 70.0,
            "max_drawdown_pct": -5.0,
        }

        state, note = promotion_state(
            metrics,
            min_fundamental_coverage_pct=50.0,
            min_profitability_coverage_pct=50.0,
            min_periods_for_paper=12,
            min_positions_for_paper=80,
        )

        self.assertEqual(state, "needs_profitability_data")
        self.assertIn("Profitability coverage", note)


if __name__ == "__main__":
    unittest.main()
