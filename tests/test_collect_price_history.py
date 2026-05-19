from __future__ import annotations

import unittest

import pandas as pd

from kr_precision_backtest.collect_price_history import (
    collect_external_price_rows,
    merge_price_history,
    normalize_fdr_ohlcv,
    normalize_pykrx_ohlcv,
    normalize_pykrx_ticker_snapshot,
    resolve_tickers,
)


class CollectPriceHistoryTest(unittest.TestCase):
    def test_normalize_fdr_ohlcv_maps_price_columns_and_estimates_market_cap(self) -> None:
        raw = pd.DataFrame(
            [
                {"Open": 1000, "High": 1100, "Low": 950, "Close": 1050, "Volume": 10000, "Change": 0.05},
            ],
            index=[pd.Timestamp("2026-05-19")],
        )

        normalized = normalize_fdr_ohlcv(
            raw,
            ticker="5930",
            metadata={"company": "삼성전자", "market": "KOSPI", "isin": "KR7005930003", "listed_shares": 1000},
        )

        row = normalized.iloc[0]
        self.assertEqual(row["ticker"], "005930")
        self.assertEqual(row["source_bas_dt"], "20260519")
        self.assertEqual(row["company"], "삼성전자")
        self.assertEqual(row["data_vendor"], "FinanceDataReader")
        self.assertEqual(float(row["trading_value"]), 10_500_000.0)
        self.assertEqual(float(row["market_cap"]), 1_050_000.0)
        self.assertEqual(float(row["day_change_pct"]), 5.0)

    def test_normalize_pykrx_ohlcv_merges_market_cap_frame(self) -> None:
        ohlcv = pd.DataFrame(
            [
                {"시가": 1000, "고가": 1100, "저가": 950, "종가": 1050, "거래량": 10000, "거래대금": 10_500_000, "등락률": 5.0},
            ],
            index=[pd.Timestamp("2026-05-19")],
        )
        cap = pd.DataFrame(
            [
                {"시가총액": 1_050_000, "상장주식수": 1000, "거래대금": 10_500_000},
            ],
            index=[pd.Timestamp("2026-05-19")],
        )

        normalized = normalize_pykrx_ohlcv(
            ohlcv,
            cap,
            ticker="005930",
            metadata={"company": "삼성전자", "market": "KOSPI"},
        )

        row = normalized.iloc[0]
        self.assertEqual(row["ticker"], "005930")
        self.assertEqual(row["source_bas_dt"], "20260519")
        self.assertEqual(row["data_vendor"], "pykrx")
        self.assertEqual(float(row["market_cap"]), 1_050_000.0)
        self.assertEqual(float(row["listed_shares"]), 1000.0)

    def test_normalize_pykrx_ticker_snapshot_maps_bulk_daily_tables(self) -> None:
        ohlcv = pd.DataFrame(
            [
                {"시가": 1000, "고가": 1100, "저가": 950, "종가": 1050, "거래량": 10000, "거래대금": 10_500_000, "등락률": 5.0},
                {"시가": 2000, "고가": 2100, "저가": 1900, "종가": 2050, "거래량": 5000, "거래대금": 10_250_000, "등락률": 2.5},
            ],
            index=["005930", "000660"],
        )
        cap = pd.DataFrame(
            [
                {"시가총액": 1_050_000, "상장주식수": 1000},
                {"시가총액": 2_050_000, "상장주식수": 1000},
            ],
            index=["005930", "000660"],
        )

        normalized = normalize_pykrx_ticker_snapshot(
            ohlcv,
            cap,
            source_bas_dt="20260519",
            metadata={
                "005930": {"company": "삼성전자", "market": "KOSPI"},
                "000660": {"company": "SK하이닉스", "market": "KOSPI"},
            },
        )

        self.assertEqual(list(normalized["ticker"]), ["000660", "005930"])
        self.assertEqual(set(normalized["source_bas_dt"]), {"20260519"})
        self.assertEqual(set(normalized["data_vendor"]), {"pykrx-bulk"})
        self.assertEqual(float(normalized[normalized["ticker"] == "000660"].iloc[0]["market_cap"]), 2_050_000.0)

    def test_merge_price_history_keeps_external_refresh_over_existing_duplicate(self) -> None:
        existing = pd.DataFrame(
            [
                {
                    "ticker": "005930",
                    "company": "삼성전자",
                    "market": "KOSPI",
                    "source_bas_dt": "20260519",
                    "open": 1000,
                    "high": 1000,
                    "low": 1000,
                    "close": 1000,
                    "volume": 1,
                    "data_vendor": "old",
                }
            ]
        )
        refreshed = pd.DataFrame(
            [
                {
                    "ticker": "005930",
                    "company": "삼성전자",
                    "market": "KOSPI",
                    "source_bas_dt": "20260519",
                    "open": 1000,
                    "high": 1100,
                    "low": 950,
                    "close": 1050,
                    "volume": 10000,
                    "data_vendor": "FinanceDataReader",
                }
            ]
        )

        merged = merge_price_history(existing, refreshed)

        self.assertEqual(len(merged), 1)
        self.assertEqual(float(merged.iloc[0]["close"]), 1050.0)
        self.assertEqual(merged.iloc[0]["data_vendor"], "FinanceDataReader")

    def test_resolve_tickers_zero_max_means_no_limit(self) -> None:
        universe = pd.DataFrame(
            {
                "ticker": ["005930", "000660", "035420"],
                "investment_universe": ["Y", "Y", "Y"],
            }
        )

        tickers = resolve_tickers("", universe, pd.DataFrame(), max_tickers=0)

        self.assertEqual(tickers, ["005930", "000660", "035420"])

    def test_resolve_tickers_prefers_latest_history_by_market_cap(self) -> None:
        universe = pd.DataFrame({"ticker": ["000010", "005930", "000660"], "investment_universe": ["Y", "Y", "Y"]})
        history = pd.DataFrame(
            [
                {"ticker": "000010", "source_bas_dt": "20260507", "market_cap": 10},
                {"ticker": "005930", "source_bas_dt": "20260507", "market_cap": 1000},
                {"ticker": "000660", "source_bas_dt": "20260507", "market_cap": 800},
            ]
        )

        tickers = resolve_tickers("", universe, history, max_tickers=2)

        self.assertEqual(tickers, ["005930", "000660"])

    def test_collect_external_price_rows_skips_inverted_date_window(self) -> None:
        rows, statuses = collect_external_price_rows(
            ["005930"],
            start="20260520",
            end="20260519",
            source="auto",
            metadata={},
        )

        self.assertTrue(rows.empty)
        self.assertEqual(statuses[0]["status"], "up_to_date")
        self.assertIn("20260520", statuses[0]["message"])


if __name__ == "__main__":
    unittest.main()
