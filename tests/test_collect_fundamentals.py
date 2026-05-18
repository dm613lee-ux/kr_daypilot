from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.collect_fundamentals import (
    extract_financial_snapshot,
    normalize_krx_fundamental_frame,
    report_availability_date,
)


class CollectFundamentalsTest(unittest.TestCase):
    def test_report_availability_date_is_conservative(self) -> None:
        self.assertEqual(report_availability_date("2024", "11011"), "20250401")
        self.assertEqual(report_availability_date("2025", "11013"), "20250516")
        self.assertEqual(report_availability_date("2025", "11012"), "20250816")
        self.assertEqual(report_availability_date("2025", "11014"), "20251116")

    def test_extract_financial_snapshot_maps_opendart_accounts(self) -> None:
        body = {
            "status": "000",
            "list": [
                {
                    "account_id": "ifrs-full_Revenue",
                    "account_nm": "매출액",
                    "sj_div": "IS",
                    "thstrm_amount": "1,000",
                    "thstrm_add_amount": "1,200",
                },
                {
                    "account_id": "dart_OperatingIncomeLoss",
                    "account_nm": "영업이익",
                    "sj_div": "IS",
                    "thstrm_amount": "100",
                    "thstrm_add_amount": "110",
                },
                {
                    "account_id": "ifrs-full_ProfitLoss",
                    "account_nm": "당기순이익",
                    "sj_div": "IS",
                    "thstrm_amount": "70",
                    "thstrm_add_amount": "80",
                },
                {
                    "account_id": "ifrs-full_Equity",
                    "account_nm": "자본총계",
                    "sj_div": "BS",
                    "thstrm_amount": "500",
                },
                {
                    "account_id": "ifrs-full_Assets",
                    "account_nm": "자산총계",
                    "sj_div": "BS",
                    "thstrm_amount": "1,500",
                },
            ],
        }

        row = extract_financial_snapshot(
            "005930",
            corp_code="00126380",
            bsns_year="2024",
            reprt_code="11011",
            fs_div="CFS",
            body=body,
            updated_at="2026-05-18T14:00:00+09:00",
        )

        self.assertEqual(row["source_bas_dt"], "20250401")
        self.assertEqual(row["ticker"], "005930")
        self.assertEqual(row["corp_code"], "00126380")
        self.assertEqual(row["revenue"], 1200.0)
        self.assertEqual(row["operating_income"], 110.0)
        self.assertEqual(row["net_income"], 80.0)
        self.assertEqual(row["equity"], 500.0)
        self.assertEqual(row["total_assets"], 1500.0)

    def test_normalize_krx_fundamental_frame_maps_pykrx_columns(self) -> None:
        raw = pd.DataFrame(
            [
                {"BPS": "10,000", "PER": "8.5", "PBR": "0.7", "EPS": "1200", "DIV": "2.3", "DPS": "300"},
            ],
            index=["005930"],
        )

        normalized = normalize_krx_fundamental_frame(raw, source_bas_dt="20260507", updated_at="2026-05-18T14:00:00+09:00")

        self.assertEqual(normalized.iloc[0]["ticker"], "005930")
        self.assertEqual(normalized.iloc[0]["source_bas_dt"], "20260507")
        self.assertEqual(normalized.iloc[0]["per"], 8.5)
        self.assertEqual(normalized.iloc[0]["pbr"], 0.7)
        self.assertEqual(normalized.iloc[0]["dividend_yield"], 2.3)


if __name__ == "__main__":
    unittest.main()
