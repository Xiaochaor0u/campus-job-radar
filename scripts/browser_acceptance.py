"""Real-browser acceptance for the internship view.

Uses the locally installed Chrome through Playwright's ``channel="chrome"``; no
Chromium download is involved.

    python scripts/seed_internops_demo.py --db %TEMP%\internops-e2e.sqlite
    RECRUITOPS_DATABASE_URL=sqlite+pysqlite:///... \\
        python -m uvicorn apps.api.main:app --port 8012
    python scripts/browser_acceptance.py --base-url http://127.0.0.1:8012

Exit code 1 on the first failed assertion; screenshots are written for review.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8012")
    parser.add_argument(
        "--shots", type=Path,
        default=PROJECT_ROOT / "docs" / "internops" / "evidence" / "ui",
    )
    parser.add_argument("--channel", default="chrome", help="chrome or msedge")
    args = parser.parse_args(argv)
    args.shots.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    steps: list[str] = []
    rows = "#jobs-table-body tr"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(args.base_url, wait_until="networkidle")
            page.wait_for_timeout(1500)
            steps.append("opened workbench")
            page.screenshot(path=str(args.shots / "01-workbench.png"))

            page.click('[data-job-nav-mode="internship"]')
            page.wait_for_selector(rows, timeout=20000)
            page.wait_for_timeout(1500)
            count = page.locator(rows).count()
            assert count >= 4, f"expected fixture internships, saw {count}"
            assert "实习" in page.locator("#jobs-table-body").inner_text()
            steps.append(f"internship view shows {count} rows")
            page.screenshot(path=str(args.shots / "02-internship-list.png"))

            page.fill("#job-city-filter", "西安")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
            after = page.locator(rows).count()
            assert 0 < after < count, f"city filter should narrow {count} -> {after}"
            body_text = page.locator("#jobs-table-body").inner_text()
            assert "郑州" not in body_text, "city filter leaked rows from 郑州"
            steps.append(f"city filter 西安 -> {after} rows")
            page.screenshot(path=str(args.shots / "03-city-filter.png"))

            page.fill("#job-city-filter", "")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)

            target = page.locator(rows).filter(has_text="自动化测试").first
            # 详情按钮是 data-job-details（复数，值是岗位 id）；.table-link 是公司筛选链接。
            target.locator("[data-job-details]").first.click()
            page.wait_for_timeout(2500)
            detail = page.locator("#job-detail-content").inner_text()
            assert "实习条件核对" in detail, detail[:200]
            for label in ("满足的条件", "存在的差距", "信息未知", "需要核实"):
                assert label in detail, f"missing section: {label}"
            assert "JD 原文" in detail, "conditions must quote the JD"
            steps.append("detail shows four condition buckets with JD evidence")
            page.screenshot(path=str(args.shots / "04-detail-conditions.png"))

            jd_text = page.locator(".job-jd").first.inner_text()
            assert "任职要求" in jd_text, "full JD must be rendered"
            href = page.locator('#job-detail-content a[href^="http"]').first.get_attribute("href")
            assert href and "example.test" in href, href
            steps.append("full JD and original source link present")

            # 关闭详情后验证投递状态入口：record 不是弹窗，而是写入
            # /api/local-ui/applications 并显示 toast、把按钮切成"查看投递"。
            # 只写本地隔离库，不联系招聘方、不提交任何申请。
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            record = page.locator(rows).filter(has_text="自动化测试").first.locator('[data-job-action="record"]')
            if record.count() != 1:
                raise AssertionError("record action missing")
            record.first.click()
            page.wait_for_timeout(2500)
            toasts = page.locator("[class*='toast'], [role='status']").all_inner_texts()
            row_text = page.locator(rows).filter(has_text="自动化测试").first.inner_text()
            assert any("投递" in item for item in toasts) or "查看投递" in row_text, (
                f"record action produced no feedback: toasts={toasts}"
            )
            steps.append("application status recorded locally (no external submission)")
            page.screenshot(path=str(args.shots / "05-application-recorded.png"))
        except Exception as exc:  # noqa: BLE001 - reported as a failure
            page.screenshot(path=str(args.shots / "99-failure.png"), full_page=True)
            print(f"[FAIL] {type(exc).__name__}: {exc}")
            print(f"page errors: {errors[:3]}")
            browser.close()
            return 1
        else:
            browser.close()

    print("== browser acceptance ==")
    for step in steps:
        print(f"[PASS] {step}")
    print(f"screenshots: {args.shots}")
    print(f"RESULT: PASSED ({len(steps)} steps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
