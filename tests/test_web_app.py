from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from kr_precision_backtest.run_web_app import (
    add_paper_position,
    build_pipeline_command,
    load_paper_ledger,
    load_dashboard_payload,
    load_ticker_detail_payload,
    save_user_decision,
    sanitize_pipeline_options,
)


class WebAppTest(unittest.TestCase):
    def test_load_dashboard_payload_reads_latest_recommendations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "investment_recommender"
            output.mkdir(parents=True)
            (output / "latest_summary.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-05-19T08:00:00+09:00",
                        "summary": {
                            "signal_day": "20260518",
                            "state": "paper_review",
                            "recommended": 1,
                            "paper_review": 1,
                            "watchlist": 0,
                            "blocked": 0,
                            "data_freshness": {"price_is_stale": False, "price_age_calendar_days": 1},
                        },
                        "recommendations": [
                            {
                                "rank": 1,
                                "ticker": "005930",
                                "company": "삼성전자",
                                "technique": "Defensive Trend Compounder",
                                "final_score": 82.7,
                                "state": "paper_review",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = load_dashboard_payload(root)

            self.assertEqual(payload["summary"]["state"], "paper_review")
            self.assertEqual(payload["recommendations"][0]["ticker"], "005930")
            self.assertEqual(payload["technique_breakdown"], {"Defensive Trend Compounder": 1})

    def test_sanitize_pipeline_options_clamps_numeric_inputs(self) -> None:
        options = sanitize_pipeline_options(
            {
                "price_max_tickers": "50000",
                "eod_max_tickers": "-3",
                "fundamental_max_tickers": "abc",
                "top": "100",
                "allow_stale_data": True,
            }
        )

        self.assertEqual(options["price_max_tickers"], 1000)
        self.assertEqual(options["eod_max_tickers"], 0)
        self.assertEqual(options["fundamental_max_tickers"], 30)
        self.assertEqual(options["top"], 50)
        self.assertTrue(options["allow_stale_data"])

    def test_build_pipeline_command_uses_only_fixed_arguments(self) -> None:
        options = sanitize_pipeline_options({"price_max_tickers": 200, "eod_max_tickers": 30, "fundamental_max_tickers": 30, "top": 15})

        command = build_pipeline_command(options, python_executable="python")

        self.assertEqual(command[:3], ["python", "-m", "kr_precision_backtest.run_recommender_pipeline"])
        self.assertIn("--price-source", command)
        self.assertIn("auto", command)
        self.assertIn("--price-max-tickers", command)
        self.assertIn("200", command)
        self.assertNotIn(";", " ".join(command))

    def test_save_user_decision_persists_status_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = save_user_decision(root, {"ticker": "5930", "status": "watch", "note": "실적 확인 후 재검토"})

            self.assertEqual(result["decision"]["ticker"], "005930")
            self.assertEqual(result["decision"]["status"], "watch")
            self.assertEqual(result["decision"]["note"], "실적 확인 후 재검토")

            payload = load_dashboard_payload(root)
            self.assertEqual(payload["user_decisions"]["005930"]["status"], "watch")

    def test_add_paper_position_and_load_ledger_marks_pnl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "investment_recommender"
            output.mkdir(parents=True)
            (output / "latest_summary.json").write_text(
                json.dumps(
                    {
                        "summary": {"signal_day": "20260518"},
                        "recommendations": [
                            {
                                "ticker": "005930",
                                "company": "삼성전자",
                                "technique": "Defensive Trend Compounder",
                                "close": 100.0,
                                "final_score": 80.0,
                                "source_bas_dt": "20260518",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            data = root / "data"
            data.mkdir()
            (data / "kr_stock_price_history.csv").write_text(
                "\n".join(
                    [
                        "ticker,company,market,source_bas_dt,open,high,low,close,volume,trading_value",
                        "005930,삼성전자,KOSPI,20260518,98,101,97,100,10,1000",
                        "005930,삼성전자,KOSPI,20260519,100,112,99,110,12,1320",
                    ]
                ),
                encoding="utf-8",
            )

            result = add_paper_position(root, {"ticker": "005930", "quantity": 3, "note": "paper 진입"})
            ledger = load_paper_ledger(root)

            self.assertTrue(result["created"])
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger[0]["ticker"], "005930")
            self.assertEqual(ledger[0]["quantity"], 3)
            self.assertEqual(ledger[0]["latest_close"], 110.0)
            self.assertAlmostEqual(ledger[0]["pnl_pct"], 10.0)
            self.assertAlmostEqual(ledger[0]["pnl_krw"], 30.0)

    def test_load_ticker_detail_payload_returns_recent_history_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "investment_recommender"
            output.mkdir(parents=True)
            (output / "latest_summary.json").write_text(
                json.dumps(
                    {
                        "recommendations": [
                            {
                                "ticker": "005930",
                                "company": "삼성전자",
                                "technique": "Defensive Trend Compounder",
                                "close": 100.0,
                                "source_bas_dt": "20260518",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            data = root / "data"
            data.mkdir()
            (data / "kr_stock_price_history.csv").write_text(
                "\n".join(
                    [
                        "ticker,company,market,source_bas_dt,open,high,low,close,volume,trading_value",
                        "005930,삼성전자,KOSPI,20260517,90,102,89,100,10,1000",
                        "005930,삼성전자,KOSPI,20260518,100,112,99,110,12,1320",
                    ]
                ),
                encoding="utf-8",
            )
            save_user_decision(root, {"ticker": "005930", "status": "exclude", "note": "이벤트 리스크"})

            payload = load_ticker_detail_payload(root, "005930")

            self.assertEqual(payload["ticker"], "005930")
            self.assertEqual(payload["decision"]["status"], "exclude")
            self.assertEqual(len(payload["history"]), 2)
            self.assertEqual(payload["history"][-1]["close"], 110.0)


if __name__ == "__main__":
    unittest.main()
