from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.run_rg2_sensitivity import (
    build_scenarios,
    summarize_sensitivity_by_strategy,
)


class Rg2SensitivityTest(unittest.TestCase):
    def test_build_scenarios_crosses_key_inputs_with_stable_ids(self) -> None:
        scenarios = build_scenarios(
            frequencies=["monthly", "weekly"],
            portfolio_sizes=[10, 20],
            slippage_pcts=[0.2],
            round_trip_cost_pct=0.6,
            max_periods=24,
        )

        self.assertEqual(len(scenarios), 4)
        self.assertEqual(scenarios[0].scenario_id, "S001")
        self.assertEqual(scenarios[0].frequency, "monthly")
        self.assertEqual(scenarios[0].portfolio_size, 10)
        self.assertEqual(scenarios[-1].scenario_id, "S004")
        self.assertEqual(scenarios[-1].frequency, "weekly")
        self.assertEqual(scenarios[-1].portfolio_size, 20)

    def test_summarize_sensitivity_marks_robust_strategy(self) -> None:
        scenario_metrics = pd.DataFrame(
            [
                {
                    "scenario_id": "S001",
                    "strategy_family": "rg2_quality_value_momentum",
                    "promotion_state": "paper_only",
                    "avg_net_return_pct": 4.0,
                    "avg_excess_return_pct": 1.2,
                    "max_drawdown_pct": -10.0,
                    "fundamental_coverage_pct": 95.0,
                    "profitability_coverage_pct": 80.0,
                },
                {
                    "scenario_id": "S002",
                    "strategy_family": "rg2_quality_value_momentum",
                    "promotion_state": "paper_only",
                    "avg_net_return_pct": 2.5,
                    "avg_excess_return_pct": 0.4,
                    "max_drawdown_pct": -16.0,
                    "fundamental_coverage_pct": 96.0,
                    "profitability_coverage_pct": 82.0,
                },
                {
                    "scenario_id": "S001",
                    "strategy_family": "rg2_defensive_low_vol",
                    "promotion_state": "failed_research",
                    "avg_net_return_pct": 1.0,
                    "avg_excess_return_pct": -0.5,
                    "max_drawdown_pct": -12.0,
                    "fundamental_coverage_pct": 92.0,
                    "profitability_coverage_pct": 76.0,
                },
            ]
        )

        summary = summarize_sensitivity_by_strategy(
            scenario_metrics,
            scenario_count=2,
            min_pass_rate_pct=60.0,
        )

        robust = summary[summary["strategy_family"] == "rg2_quality_value_momentum"].iloc[0]
        failed = summary[summary["strategy_family"] == "rg2_defensive_low_vol"].iloc[0]
        self.assertEqual(robust["robustness_state"], "robust_paper_only")
        self.assertEqual(robust["paper_only_scenarios"], 2)
        self.assertEqual(robust["pass_rate_pct"], 100.0)
        self.assertEqual(failed["robustness_state"], "not_robust")


if __name__ == "__main__":
    unittest.main()
