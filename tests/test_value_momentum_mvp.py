from __future__ import annotations

import json
import unittest

import pandas as pd

from kr_precision_backtest.run_value_momentum_mvp import json_ready
from kr_precision_backtest.value_momentum_mvp import (
    ValueMomentumConfig,
    add_value_momentum_features,
    score_value_momentum_candidates,
    select_value_momentum_portfolio,
)


class ValueMomentumMvpTest(unittest.TestCase):
    def test_price_features_include_6_to_12_month_momentum(self) -> None:
        rows = [
            {
                "source_bas_dt": f"2025{((idx - 1) // 20) + 1:02d}{((idx - 1) % 20) + 1:02d}",
                "ticker": "000001",
                "close": float(100 + idx),
                "trading_value": 10_000_000_000.0,
            }
            for idx in range(1, 261)
        ]

        featured = add_value_momentum_features(pd.DataFrame(rows))

        self.assertIn("ret_120d_pct", featured.columns)
        self.assertIn("ret_240d_pct", featured.columns)
        self.assertIn("relative_momentum_120d_pct", featured.columns)
        self.assertIn("relative_momentum_240d_pct", featured.columns)
        self.assertTrue(featured["ret_120d_pct"].notna().any())
        self.assertTrue(featured["ret_240d_pct"].notna().any())

    def test_scoring_prefers_low_valuation_high_roe_and_momentum(self) -> None:
        day_rows = pd.DataFrame(
            [
                {
                    "ticker": "000001",
                    "company": "A",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 30_000_000_000,
                    "per": 6.0,
                    "pbr": 0.7,
                    "roe": 0.18,
                    "ret_120d_pct": 24.0,
                    "ret_240d_pct": 40.0,
                    "relative_momentum_120d_pct": 12.0,
                    "relative_momentum_240d_pct": 18.0,
                    "disclosure_risk_flag": False,
                },
                {
                    "ticker": "000002",
                    "company": "B",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 30_000_000_000,
                    "per": 22.0,
                    "pbr": 2.8,
                    "roe": 0.04,
                    "ret_120d_pct": -2.0,
                    "ret_240d_pct": 1.0,
                    "relative_momentum_120d_pct": -8.0,
                    "relative_momentum_240d_pct": -10.0,
                    "disclosure_risk_flag": False,
                },
            ]
        )

        selected = select_value_momentum_portfolio(day_rows, ValueMomentumConfig(), portfolio_size=1)

        self.assertEqual(selected.iloc[0]["ticker"], "000001")
        self.assertEqual(selected.iloc[0]["candidate_status"], "pass")
        self.assertGreater(selected.iloc[0]["vm_composite_score"], 90.0)

    def test_risk_disclosure_and_invalid_valuation_are_blocked(self) -> None:
        day_rows = pd.DataFrame(
            [
                {
                    "ticker": "000001",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 30_000_000_000,
                    "per": 6.0,
                    "pbr": 0.7,
                    "roe": 0.18,
                    "ret_120d_pct": 24.0,
                    "disclosure_risk_flag": True,
                },
                {
                    "ticker": "000002",
                    "market": "KOSPI",
                    "market_cap": 500_000_000_000,
                    "avg_value_20": 30_000_000_000,
                    "per": 0.0,
                    "pbr": 0.7,
                    "roe": 0.18,
                    "ret_120d_pct": 24.0,
                    "disclosure_risk_flag": False,
                },
            ]
        )

        scored = score_value_momentum_candidates(day_rows, ValueMomentumConfig())

        self.assertEqual(scored[scored["ticker"] == "000001"].iloc[0]["candidate_status"], "blocked")
        self.assertIn("disclosure_risk", scored[scored["ticker"] == "000001"].iloc[0]["block_reason"])
        self.assertIn("invalid_per", scored[scored["ticker"] == "000002"].iloc[0]["block_reason"])

    def test_json_ready_removes_non_standard_nan_values(self) -> None:
        payload = {"rows": [{"ticker": "000001", "isin": float("nan"), "roe": pd.NA}]}

        cleaned = json_ready(payload)

        self.assertIsNone(cleaned["rows"][0]["isin"])
        self.assertIsNone(cleaned["rows"][0]["roe"])
        json.dumps(cleaned, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
