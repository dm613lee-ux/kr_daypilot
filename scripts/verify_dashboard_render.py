from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET_HTML = PROJECT_ROOT / "output" / "dashboard" / "latest.html"
OUTPUT_DIR = PROJECT_ROOT / "output" / "browser_checks"


def main() -> int:
    if not TARGET_HTML.exists():
        raise FileNotFoundError(f"HTML file not found: {TARGET_HTML}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = OUTPUT_DIR / f"dashboard_render_{datetime.now():%Y%m%d_%H%M%S}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1365, "height": 768})
        page.goto(TARGET_HTML.resolve().as_uri(), wait_until="load")
        title = page.title()
        h1 = page.locator("h1").first.inner_text()
        body_text = page.locator("body").inner_text()
        card_count = page.locator(".card").count()
        table_count = page.locator("table").count()
        screenshot = page.screenshot(path=str(screenshot_path), full_page=True)
        browser.close()

    checks = {
        "title": "KR DayPilot" in title,
        "h1": "KR DayPilot 누적 성과 대시보드" in h1,
        "success_rate_label": "후보 기준 성공률" in body_text,
        "recent_section": "최근 추천 결과" in body_text,
        "reason_section": "실패·무진입 원인" in body_text,
        "cards": card_count >= 8,
        "tables": table_count >= 3,
        "encoding": "�" not in body_text,
        "screenshot": len(screenshot) > 10000,
    }
    failed = [name for name, ok in checks.items() if not ok]
    result = {
        "ok": not failed,
        "title": title,
        "h1": h1,
        "cardCount": card_count,
        "tableCount": table_count,
        "screenshotPath": str(screenshot_path),
        "failedChecks": failed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
