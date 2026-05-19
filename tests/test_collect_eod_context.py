from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.collect_eod_context import INVESTOR_COLUMNS, merge_context_frames


class CollectEodContextTest(unittest.TestCase):
    def test_merge_context_frames_preserves_existing_rows_and_updates_duplicates(self) -> None:
        existing = pd.DataFrame(
            [
                {
                    "source_bas_dt": "20260517",
                    "ticker": "005930",
                    "foreign_net_buy_value": 100.0,
                    "institution_net_buy_value": 50.0,
                    "retail_net_buy_value": -150.0,
                    "source": "old",
                    "updated_at": "old",
                },
                {
                    "source_bas_dt": "20260518",
                    "ticker": "005930",
                    "foreign_net_buy_value": 100.0,
                    "institution_net_buy_value": 50.0,
                    "retail_net_buy_value": -150.0,
                    "source": "old",
                    "updated_at": "old",
                },
            ]
        )
        new = pd.DataFrame(
            [
                {
                    "source_bas_dt": "20260518",
                    "ticker": "005930",
                    "foreign_net_buy_value": 200.0,
                    "institution_net_buy_value": 70.0,
                    "retail_net_buy_value": -270.0,
                    "source": "new",
                    "updated_at": "new",
                }
            ]
        )

        merged = merge_context_frames(existing, new, INVESTOR_COLUMNS, key_columns=["source_bas_dt", "ticker"])

        self.assertEqual(len(merged), 2)
        updated = merged[merged["source_bas_dt"] == "20260518"].iloc[0]
        self.assertEqual(float(updated["foreign_net_buy_value"]), 200.0)
        self.assertEqual(updated["source"], "new")


if __name__ == "__main__":
    unittest.main()
