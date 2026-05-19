from __future__ import annotations

import json
import unittest

import pandas as pd

from kr_precision_backtest.investment_recommender import (
    InvestmentRecommenderConfig,
    build_recommendations,
    json_ready,
    score_day_rows,
)


class InvestmentRecommenderTest(unittest.TestCase):
    def test_score_day_rows_assigns_named_investment_technique(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "ticker": "000001",
                    "company": "Quality Value",
                    "market": "KOSPI",
                    "close": 10000,
                    "market_cap": 800_000_000_000,
                    "avg_value_20": 30_000_000_000,
                    "per": 7.0,
                    "pbr": 0.7,
                    "dividend_yield": 2.0,
                    "roe": 0.18,
                    "roa": 0.08,
                    "operating_margin": 0.12,
                    "net_margin": 0.08,
                    "relative_momentum_120d_pct": 14.0,
                    "relative_momentum_240d_pct": 20.0,
                    "ret_20d_pct": 6.0,
                    "ret_60d_pct": 18.0,
                    "volatility_60d_pct": 2.0,
                    "drawdown_60d_pct": -3.0,
                    "price_vs_ma120_pct": 15.0,
                    "smart_flow_20d_pressure_pct": 2.0,
                    "disclosure_risk_flag": False,
                    "investment_universe": "Y",
                },
                {
                    "ticker": "000002",
                    "company": "Expensive Flow",
                    "market": "KOSPI",
                    "close": 20000,
                    "market_cap": 900_000_000_000,
                    "avg_value_20": 35_000_000_000,
                    "per": 35.0,
                    "pbr": 4.5,
                    "dividend_yield": 0.0,
                    "roe": 0.05,
                    "roa": 0.02,
                    "operating_margin": 0.03,
                    "net_margin": 0.02,
                    "relative_momentum_120d_pct": 10.0,
                    "relative_momentum_240d_pct": 14.0,
                    "ret_20d_pct": 5.0,
                    "ret_60d_pct": 16.0,
                    "volatility_60d_pct": 3.0,
                    "drawdown_60d_pct": -5.0,
                    "price_vs_ma120_pct": 11.0,
                    "smart_flow_20d_pressure_pct": 18.0,
                    "disclosure_risk_flag": False,
                    "investment_universe": "Y",
                },
            ]
        )

        scored = score_day_rows(rows, InvestmentRecommenderConfig(min_score_for_review=0))

        techniques = dict(zip(scored["ticker"], scored["technique"]))
        self.assertEqual(techniques["000001"], "Quality Value Momentum")
        self.assertEqual(techniques["000002"], "Flow-Backed Re-Rating")
        self.assertIn("score_components", scored.columns)
        self.assertIn("evidence_summary", scored.columns)

    def test_risk_disclosure_blocks_candidate_even_when_factors_are_strong(self) -> None:
        rows = pd.DataFrame(
            [
                {
                    "ticker": "000001",
                    "company": "Risky Winner",
                    "market": "KOSPI",
                    "close": 10000,
                    "market_cap": 800_000_000_000,
                    "avg_value_20": 30_000_000_000,
                    "per": 6.0,
                    "pbr": 0.6,
                    "roe": 0.20,
                    "relative_momentum_120d_pct": 30.0,
                    "relative_momentum_240d_pct": 45.0,
                    "volatility_60d_pct": 2.0,
                    "drawdown_60d_pct": -2.0,
                    "smart_flow_20d_pressure_pct": 20.0,
                    "disclosure_risk_flag": True,
                    "investment_universe": "Y",
                }
            ]
        )

        scored = score_day_rows(rows, InvestmentRecommenderConfig(min_score_for_review=0))

        self.assertEqual(scored.iloc[0]["state"], "blocked")
        self.assertIn("disclosure_risk", scored.iloc[0]["block_reason"])

    def test_build_recommendations_uses_only_fundamentals_available_by_asof(self) -> None:
        history_rows = self._history_rows()
        fundamentals = pd.DataFrame(
            [
                {
                    "source_bas_dt": "20250331",
                    "ticker": "000001",
                    "revenue": 1000.0,
                    "operating_income": 100.0,
                    "net_income": 80.0,
                    "equity": 400.0,
                    "total_assets": 1000.0,
                },
                {
                    "source_bas_dt": "20250731",
                    "ticker": "000001",
                    "revenue": 1000.0,
                    "operating_income": 500.0,
                    "net_income": 500.0,
                    "equity": 400.0,
                    "total_assets": 1000.0,
                },
            ]
        )
        valuation = pd.DataFrame(
            [
                {"source_bas_dt": "20250331", "ticker": "000001", "per": 8.0, "pbr": 0.9, "dividend_yield": 1.5},
                {"source_bas_dt": "20250731", "ticker": "000001", "per": 2.0, "pbr": 0.2, "dividend_yield": 8.0},
            ]
        )

        recommendations, summary = build_recommendations(
            pd.DataFrame(history_rows),
            fundamentals=fundamentals,
            valuation=valuation,
            investor_flows=pd.DataFrame(),
            disclosures=pd.DataFrame(),
            universe=pd.DataFrame({"ticker": ["000001"], "investment_universe": ["Y"], "sector": ["IT"]}),
            config=InvestmentRecommenderConfig(min_score_for_review=0),
            as_of="20250630",
            top=5,
        )

        self.assertEqual(summary["as_of"], "20250630")
        self.assertEqual(recommendations.iloc[0]["fundamental_asof_dt"], "20250331")
        self.assertEqual(recommendations.iloc[0]["valuation_asof_dt"], "20250331")
        self.assertAlmostEqual(float(recommendations.iloc[0]["roe"]), 0.2)
        self.assertEqual(float(recommendations.iloc[0]["per"]), 8.0)

    def test_stale_price_data_blocks_paper_review_by_default(self) -> None:
        recommendations, summary = build_recommendations(
            pd.DataFrame(self._history_rows()),
            fundamentals=pd.DataFrame(),
            valuation=pd.DataFrame(),
            investor_flows=pd.DataFrame(),
            disclosures=pd.DataFrame(),
            universe=pd.DataFrame({"ticker": ["000001"], "investment_universe": ["Y"], "sector": ["IT"]}),
            config=InvestmentRecommenderConfig(min_score_for_review=0, max_price_age_calendar_days=7),
            as_of="20250630",
            run_date="20250715",
            top=5,
        )

        self.assertTrue(recommendations.empty)
        self.assertEqual(summary["state"], "stale_data")
        self.assertEqual(summary["recommended"], 0)
        self.assertTrue(summary["data_freshness"]["price_is_stale"])
        self.assertEqual(summary["data_freshness"]["price_age_calendar_days"], 15)
        self.assertGreater(summary["blocked"], 0)

    def test_allow_stale_price_data_keeps_recommendations_but_marks_freshness(self) -> None:
        recommendations, summary = build_recommendations(
            pd.DataFrame(self._history_rows()),
            fundamentals=pd.DataFrame(),
            valuation=pd.DataFrame(),
            investor_flows=pd.DataFrame(),
            disclosures=pd.DataFrame(),
            universe=pd.DataFrame({"ticker": ["000001"], "investment_universe": ["Y"], "sector": ["IT"]}),
            config=InvestmentRecommenderConfig(
                min_score_for_review=0,
                max_price_age_calendar_days=7,
                allow_stale_price_data=True,
            ),
            as_of="20250630",
            run_date="20250715",
            top=5,
        )

        self.assertFalse(recommendations.empty)
        self.assertEqual(summary["state"], "paper_review_stale")
        self.assertTrue(summary["data_freshness"]["price_is_stale"])

    def test_json_ready_removes_nan_and_pandas_na(self) -> None:
        payload = {"items": [{"ticker": "000001", "score": float("nan"), "reason": pd.NA}]}

        cleaned = json_ready(payload)

        self.assertIsNone(cleaned["items"][0]["score"])
        self.assertIsNone(cleaned["items"][0]["reason"])
        json.dumps(cleaned, allow_nan=False)

    def _history_rows(self) -> list[dict[str, object]]:
        rows = []
        for idx in range(1, 181):
            day = f"2025{((idx - 1) // 30) + 1:02d}{((idx - 1) % 30) + 1:02d}"
            rows.append(
                {
                    "ticker": "000001",
                    "company": "AsOf Corp",
                    "market": "KOSPI",
                    "source_bas_dt": day,
                    "open": 100 + idx,
                    "high": 102 + idx,
                    "low": 99 + idx,
                    "close": 101 + idx,
                    "volume": 1_000_000,
                    "trading_value": 20_000_000_000,
                    "market_cap": 500_000_000_000,
                }
            )
        return rows


if __name__ == "__main__":
    unittest.main()
