from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.fundamental_core_engine import (
    FundamentalCoreConfig,
    score_core_day_rows,
)


class FundamentalCoreEngineTest(unittest.TestCase):
    def test_core_engine_prioritizes_fundamental_core_over_tactical_flow(self) -> None:
        rows = pd.DataFrame(
            [
                self._row(
                    "000001",
                    per=6.0,
                    pbr=0.6,
                    dividend_yield=3.0,
                    roe=0.18,
                    roa=0.09,
                    operating_margin=0.16,
                    net_margin=0.10,
                    relative_momentum_120d_pct=12.0,
                    relative_momentum_240d_pct=18.0,
                    ret_60d_pct=10.0,
                    price_vs_ma120_pct=8.0,
                    volatility_60d_pct=2.0,
                    drawdown_60d_pct=-4.0,
                    smart_flow_20d_pressure_pct=1.0,
                ),
                self._row(
                    "000002",
                    per=60.0,
                    pbr=5.0,
                    dividend_yield=0.0,
                    roe=0.04,
                    roa=0.02,
                    operating_margin=0.03,
                    net_margin=0.02,
                    relative_momentum_120d_pct=35.0,
                    relative_momentum_240d_pct=50.0,
                    ret_60d_pct=28.0,
                    price_vs_ma120_pct=25.0,
                    volatility_60d_pct=3.0,
                    drawdown_60d_pct=-5.0,
                    smart_flow_20d_pressure_pct=30.0,
                ),
            ]
        )

        scored = score_core_day_rows(rows, FundamentalCoreConfig(min_score_for_review=0))

        by_ticker = {row["ticker"]: row for row in scored.to_dict("records")}
        self.assertEqual(by_ticker["000001"]["technique"], "Fundamental Core Composite")
        self.assertGreater(by_ticker["000001"]["final_score"], by_ticker["000002"]["final_score"])
        self.assertGreater(by_ticker["000001"]["fundamental_score"], by_ticker["000002"]["fundamental_score"])
        self.assertIn("value_momentum_score", scored.columns)
        self.assertIn("event_flow_confirmation_score", scored.columns)

    def test_core_engine_blocks_severe_trend_and_volatility_risk(self) -> None:
        rows = pd.DataFrame(
            [
                self._row(
                    "000001",
                    per=5.0,
                    pbr=0.5,
                    dividend_yield=3.2,
                    roe=0.21,
                    roa=0.11,
                    operating_margin=0.18,
                    net_margin=0.12,
                    relative_momentum_120d_pct=-40.0,
                    relative_momentum_240d_pct=-45.0,
                    ret_60d_pct=-25.0,
                    price_vs_ma120_pct=-30.0,
                    volatility_60d_pct=14.0,
                    drawdown_60d_pct=-45.0,
                    smart_flow_20d_pressure_pct=0.0,
                )
            ]
        )

        scored = score_core_day_rows(rows, FundamentalCoreConfig(min_score_for_review=0))

        self.assertEqual(scored.iloc[0]["state"], "blocked")
        self.assertIn("core_trend_break", scored.iloc[0]["block_reason"])
        self.assertIn("core_high_volatility", scored.iloc[0]["block_reason"])

    def _row(self, ticker: str, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "ticker": ticker,
            "company": ticker,
            "market": "KOSPI",
            "close": 10000.0,
            "market_cap": 800_000_000_000.0,
            "avg_value_20": 30_000_000_000.0,
            "ret_20d_pct": 4.0,
            "smart_flow_5d_pressure_pct": 0.0,
            "disclosure_risk_flag": False,
            "positive_event_flag": False,
            "investment_universe": "Y",
        }
        row.update(overrides)
        return row


if __name__ == "__main__":
    unittest.main()
