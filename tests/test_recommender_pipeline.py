from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

import pandas as pd

from kr_precision_backtest.run_recommender_pipeline import (
    PipelineConfig,
    build_pipeline_steps,
    resolve_pipeline_window,
    write_pipeline_summary,
)


class RecommenderPipelineTest(unittest.TestCase):
    def test_resolve_pipeline_window_uses_latest_price_day_plus_one(self) -> None:
        history = pd.DataFrame(
            [
                {"ticker": "005930", "source_bas_dt": "20260507"},
                {"ticker": "000660", "source_bas_dt": "20260503"},
            ]
        )

        window = resolve_pipeline_window(history, from_date="", to_date="", run_date="20260519")

        self.assertEqual(window["from_date"], "20260508")
        self.assertEqual(window["to_date"], "20260519")
        self.assertEqual(window["run_date"], "20260519")

    def test_resolve_pipeline_window_does_not_return_inverted_dates_when_history_is_current(self) -> None:
        history = pd.DataFrame(
            [
                {"ticker": "005930", "source_bas_dt": "20260519"},
                {"ticker": "000660", "source_bas_dt": "20260518"},
            ]
        )

        window = resolve_pipeline_window(history, from_date="", to_date="", run_date="20260519")

        self.assertEqual(window["from_date"], "20260519")
        self.assertEqual(window["to_date"], "20260519")
        self.assertEqual(window["run_date"], "20260519")

    def test_build_pipeline_steps_orders_refreshes_before_recommender(self) -> None:
        config = PipelineConfig(
            program_root=Path("X:/kr_daypilot"),
            from_date="20260508",
            to_date="20260519",
            run_date="20260519",
            tickers="005930,000660",
            price_source="pykrx-bulk",
            price_max_tickers=0,
            eod_max_tickers=25,
            fundamental_max_tickers=25,
            recommendation_top=15,
        )

        steps = build_pipeline_steps(config, python_executable="python")

        self.assertEqual([step.name for step in steps], ["price_refresh", "eod_context", "fundamentals", "investment_recommender", "fundamental_core"])
        self.assertIn("kr_precision_backtest.collect_price_history", steps[0].command)
        self.assertIn("--source", steps[0].command)
        self.assertIn("pykrx-bulk", steps[0].command)
        self.assertIn("--tickers", steps[0].command)
        self.assertIn("005930,000660", steps[0].command)
        self.assertIn("--run-date", steps[-1].command)
        self.assertIn("20260519", steps[-1].command)
        self.assertIn("kr_precision_backtest.run_fundamental_core", steps[-1].command)
        self.assertTrue(steps[0].required)
        self.assertFalse(steps[1].required)
        self.assertFalse(steps[2].required)

    def test_write_pipeline_summary_serializes_path_config(self) -> None:
        config = PipelineConfig(
            program_root=Path("X:/kr_daypilot"),
            from_date="20260508",
            to_date="20260519",
            run_date="20260519",
        )
        statuses = [{"name": "price_refresh", "command": ["python"], "required": True, "returncode": 0}]

        with tempfile.TemporaryDirectory() as tmp:
            path = write_pipeline_summary(statuses, config, Path(tmp))

            self.assertTrue(path.exists())
            self.assertIn('"program_root": "X:', path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
