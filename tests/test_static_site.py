from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from kr_precision_backtest.build_static_site import build_static_site
from kr_precision_backtest.run_web_app import save_user_decision


class StaticSiteBuildTest(unittest.TestCase):
    def test_build_static_site_exports_public_dashboard_without_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_webapp(root)
            output = root / "output" / "investment_recommender"
            core = root / "output" / "fundamental_core"
            output.mkdir(parents=True)
            core.mkdir(parents=True)
            (output / "latest_summary.json").write_text(
                json.dumps(
                    {
                        "summary": {"signal_day": "20260518", "state": "paper_review", "paper_review": 1},
                        "recommendations": [
                            {
                                "ticker": "005930",
                                "company": "삼성전자",
                                "market": "KOSPI",
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
            (core / "latest_summary.json").write_text(
                json.dumps(
                    {
                        "summary": {"signal_day": "20260518", "state": "paper_review", "paper_review": 1},
                        "recommendations": [
                            {
                                "ticker": "005930",
                                "company": "삼성전자",
                                "market": "KOSPI",
                                "technique": "Fundamental Core Composite",
                                "close": 100.0,
                                "final_score": 82.0,
                                "source_bas_dt": "20260518",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (output / "latest.html").write_text("<html>latest</html>", encoding="utf-8")
            (core / "latest.html").write_text("<html>core</html>", encoding="utf-8")
            data = root / "data"
            data.mkdir()
            (data / "kr_stock_price_history.csv").write_text(
                "\n".join(
                    [
                        "ticker,company,market,source_bas_dt,open,high,low,close,volume,trading_value",
                        "005930,삼성전자,KOSPI,20260517,90,101,89,95,10,950",
                        "005930,삼성전자,KOSPI,20260518,95,102,94,100,12,1200",
                    ]
                ),
                encoding="utf-8",
            )
            save_user_decision(root, {"ticker": "005930", "status": "watch", "note": "private memo"})

            result = build_static_site(root, root / "site")

            self.assertEqual(result["ticker_files"], 1)
            self.assertTrue((root / "site" / "index.html").exists())
            self.assertTrue((root / "site" / "app.js").exists())
            self.assertTrue((root / "site" / "data" / "dashboard.json").exists())
            self.assertTrue((root / "site" / "data" / "tickers" / "005930.json").exists())
            self.assertTrue((root / "site" / "reports" / "latest.html").exists())
            dashboard = json.loads((root / "site" / "data" / "dashboard.json").read_text(encoding="utf-8"))
            ticker = json.loads((root / "site" / "data" / "tickers" / "005930.json").read_text(encoding="utf-8"))

            self.assertEqual(dashboard["deployment"]["mode"], "github_pages_static")
            self.assertEqual(dashboard["user_decisions"], {})
            self.assertEqual(dashboard["paper_ledger"], [])
            self.assertEqual(dashboard["recommendations"][0]["user_note"], "")
            self.assertEqual(ticker["decision"]["note"], "")
            self.assertEqual(dashboard["files"]["latest_report_html"], "reports/latest.html")
            self.assertNotIn(str(root), json.dumps(dashboard, ensure_ascii=False))
            self.assertNotIn("private memo", json.dumps(dashboard, ensure_ascii=False))

            index_html = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("window.KR_DAYPILOT_STATIC = true", index_html)
            self.assertIn('href="app.css', index_html)
            self.assertIn('src="app.js', index_html)

    def _write_webapp(self, root: Path) -> None:
        webapp = root / "webapp"
        webapp.mkdir()
        (webapp / "index.html").write_text(
            """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="/app.css?v=test">
</head>
<body>
  <a id="latestReportLink" href="/report/latest.html">report</a>
  <script src="/app.js?v=test"></script>
</body>
</html>
""",
            encoding="utf-8",
        )
        (webapp / "app.js").write_text("window.testApp = true;\n", encoding="utf-8")
        (webapp / "app.css").write_text("body { color: #111; }\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
