#!/usr/bin/env python3
"""Recaptures every screenshot in frontend/public/user-manual/{en,ja}.md
against the real running app, so the manual can be refreshed whenever the
UI drifts out from under it (as it did -- a sidebar redesign and the
vehicle-clustering removal both went unnoticed in the manual's images for
weeks) instead of by hand, scene by scene.

Requires: the frontend dev server reachable at --base (default
http://localhost:5174, i.e. `make frontend-dev`), agency 1 holding real
data, and `login_required` off for the duration of the run -- the manual
is captured as a guest, matching how a first-time visitor actually sees
it. Toggling that flag is a `feature_flags` write, which the dev-database
guard blocks from an agent's own tool calls; run this script via the `!`
prefix (a human-run command), or from a session with that guard disabled.

Usage:
    poetry run python scripts/capture_user_manual_screenshots.py [--locale ja|en|both] [--base URL]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, ViewportSize, sync_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "user-manual"
VIEWPORT: ViewportSize = {"width": 1440, "height": 812}
AGENCY_ID = 1
ROUTE_FOR_CERTIFICATE = "33101"

# One label per interactive step, per locale -- each is a real accessible
# name or visible string read back from the i18n locale files, not a guess.
LABELS = {
    "ja": {
        "period": "8/20",
        "hint": "ヒント",
        "on_time": "定時率",
        "agencies_tab": "事業者どうし",
        "you_badge": "あなた",
        "later": "後で",
        "ask_card": "遅延の大きい路線：上位5件",
        "returned_data": "取得したデータ",
        "council_tab": "協議会向けまとめ・遅延証明",
        "certificate": "遅延証明書",
        "route_label": "路線",
        "route_placeholder": "路線を検索",
    },
    "en": {
        "period": "8/20",
        "hint": "Hint",
        "on_time": "On-time rate",
        "agencies_tab": "Agencies",
        "you_badge": "YOU",
        "later": "Later",
        "ask_card": "most delayed routes",
        "returned_data": "View returned data",
        "council_tab": "Council report",
        "certificate": "Delay certificate",
        "route_label": "Route",
        "route_placeholder": "Search routes",
    },
}


def shot(page: Page, name: str, locale: str) -> None:
    path = OUT_DIR / f"{name}.{locale}.png"
    page.screenshot(path=str(path))
    print(f"wrote {path}")


def capture(base: str, locale: str) -> None:
    L = LABELS[locale]
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 01: fresh context -- no agency chosen yet.
        ctx = browser.new_context(viewport=VIEWPORT)
        ctx.add_init_script(f"window.localStorage.setItem('app.locale', '{locale}')")
        page = ctx.new_page()
        page.goto(f"{base}/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        shot(page, "01-agency-select", locale)
        ctx.close()

        # Every other scene: agency already chosen via the URL.
        ctx = browser.new_context(viewport=VIEWPORT)
        ctx.add_init_script(f"window.localStorage.setItem('app.locale', '{locale}')")
        page = ctx.new_page()
        agency = f"{base}/agencies/{AGENCY_ID}"

        page.goto(f"{agency}/pulse")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        shot(page, "02-pulse", locale)

        # 03: scope sentence, period popover open.
        page.get_by_role("button", name=L["period"], exact=False).first.click()
        page.wait_for_timeout(400)
        shot(page, "03-scope-sentence", locale)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        page.goto(f"{agency}/routes")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        shot(page, "05-routes-ranking", locale)

        # 06: the report-list hint popover.
        page.get_by_label(L["hint"]).first.click()
        page.wait_for_timeout(400)
        shot(page, "06-reports-hint", locale)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # 08: the on-time-rate report.
        page.get_by_text(L["on_time"], exact=True).first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        shot(page, "08-routes-ontime", locale)

        page.goto(f"{agency}/time")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        shot(page, "07-time-trend", locale)

        page.goto(f"{agency}/why")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        shot(page, "09-why", locale)

        # 10: compare -> agencies tab, waiting for the ranked table (not
        # just the tab switch) to actually render.
        page.goto(f"{agency}/compare")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        page.get_by_text(L["agencies_tab"], exact=True).first.click()
        page.get_by_text(L["you_badge"], exact=True).first.wait_for(timeout=15000)
        page.wait_for_timeout(500)
        shot(page, "10-compare-agencies", locale)

        # 11: live map. Dismissing the first-time tour is best-effort --
        # the feed may be quiet at capture time regardless, which is itself
        # a real, documented state (see the manual's own "no vehicles"
        # paragraph), not a capture failure.
        page.goto(f"{agency}/live")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        later = page.get_by_text(L["later"], exact=True)
        if later.count() > 0:
            later.first.click()
            page.wait_for_timeout(300)
        page.wait_for_timeout(2000)
        shot(page, "11-live", locale)

        page.goto(f"{agency}/ask")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        shot(page, "12-ask-landing", locale)

        # 13: answer a ready-made question, waiting for the real result
        # (not just the loading state) before capturing.
        page.get_by_text(L["ask_card"], exact=False).first.click()
        page.get_by_text(L["returned_data"], exact=False).first.wait_for(timeout=20000)
        page.wait_for_timeout(500)
        shot(page, "13-ask-answer", locale)

        # 15: the delay-certificate's full three-step flow -- route, then
        # whichever departure its own data returns.
        page.goto(f"{agency}/reports")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        page.get_by_text(L["council_tab"], exact=False).first.click()
        page.wait_for_timeout(500)
        page.get_by_text(L["certificate"], exact=True).first.click()
        page.wait_for_timeout(800)
        page.get_by_label(L["route_label"], exact=True).click()
        page.wait_for_timeout(300)
        page.get_by_placeholder(L["route_placeholder"], exact=False).fill(ROUTE_FOR_CERTIFICATE)
        page.wait_for_timeout(500)
        page.get_by_role("option").first.click()
        page.wait_for_timeout(1500)
        select = page.locator("select").last
        select.wait_for(timeout=15000)
        options = select.locator("option").all()
        if len(options) > 1:
            value = options[1].get_attribute("value")
            select.select_option(value=value)
        page.wait_for_timeout(1000)
        shot(page, "15-reports-certificate", locale)

        ctx.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locale", choices=["ja", "en", "both"], default="both")
    parser.add_argument("--base", default="http://localhost:5174")
    args = parser.parse_args()
    locales = ["ja", "en"] if args.locale == "both" else [args.locale]
    for locale in locales:
        capture(args.base, locale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
