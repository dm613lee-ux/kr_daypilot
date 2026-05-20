from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.audit_score_coverage import (
    add_validity_flags,
    audit_score_columns,
)


class ScoreCoverageAuditTest(unittest.TestCase):
    def test_flow_score_marks_missing_asof_as_neutral_fallback(self) -> None:
        day_rows = pd.DataFrame(
            [
                {"ticker": "000001", "smart_flow_asof_dt": "20260518"},
                {"ticker": "000002", "smart_flow_asof_dt": ""},
            ]
        )
        tactical = pd.DataFrame(
            [
                {"flow_score": 80.0, "raw_final_score": 80.0, "value_score": 70.0, "quality_score": 70.0, "momentum_score": 70.0, "defensive_score": 70.0, "liquidity_score": 70.0},
                {"flow_score": 50.0, "raw_final_score": 50.0, "value_score": 70.0, "quality_score": 70.0, "momentum_score": 70.0, "defensive_score": 70.0, "liquidity_score": 70.0},
            ]
        )
        core = pd.DataFrame([{"raw_final_score": 75.0}, {"raw_final_score": 55.0}])

        flags = add_validity_flags(day_rows, tactical, core)
        rows = audit_score_columns(tactical, flags, "shared", ["flow_score"])

        self.assertEqual(rows[0]["fallback_neutral_count"], 1)
        self.assertEqual(rows[0]["fallback_neutral_pct"], 50.0)

    def test_event_score_marks_no_event_as_neutral_fallback(self) -> None:
        day_rows = pd.DataFrame(
            [
                {"ticker": "000001", "positive_event_flag": True, "disclosure_risk_flag": False},
                {"ticker": "000002", "positive_event_flag": False, "disclosure_risk_flag": False},
            ]
        )
        tactical = pd.DataFrame(
            [
                {"event_score": 105.0, "raw_final_score": 80.0, "value_score": 70.0, "quality_score": 70.0, "momentum_score": 70.0, "defensive_score": 70.0, "liquidity_score": 70.0},
                {"event_score": 100.0, "raw_final_score": 70.0, "value_score": 70.0, "quality_score": 70.0, "momentum_score": 70.0, "defensive_score": 70.0, "liquidity_score": 70.0},
            ]
        )
        core = pd.DataFrame([{"raw_final_score": 75.0}, {"raw_final_score": 70.0}])

        flags = add_validity_flags(day_rows, tactical, core)
        rows = audit_score_columns(tactical, flags, "shared", ["event_score"])

        self.assertEqual(rows[0]["fallback_neutral_count"], 1)
        self.assertEqual(rows[0]["raw_full_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
