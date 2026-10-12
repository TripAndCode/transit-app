#!/usr/bin/env python3
"""Recaptures every screenshot in frontend/public/user-manual/{en,ja}.md
against the real running app, so the manual can be refreshed by running one
command instead of by hand, scene by scene -- the manual's screenshots have
no automated staleness check, so a UI change can drift out from under them
indefinitely otherwise.

Requires, in order:
  1. A production build served at --base (default http://localhost:8000,
     i.e. `cd frontend && npm run build && cd .. && make bake`, then a
     running `make serve`/uvicorn backend). The dev server mounts a
     dev-only sidebar section (`import.meta.env.DEV`) that a production
     build never shows -- capturing against it would document a debug
     shortcut menu as if it were part of the real end-user sidebar.
  2. Agency 1 holding real data.
  3. `login_required` off for the run -- the manual is captured as a guest,
     matching how a first-time visitor actually sees it. Toggling that
     flag is a separate, human `feature_flags` admin action (the dev-
     database guard blocks the write from an agent's own tool calls); flip
     it via the admin flags UI/API before running this script, and back on
     once it finishes. This script never touches that flag itself.

Usage:
    poetry run python scripts/capture_user_manual_screenshots.py [--locale ja|en|both] [--base URL]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import Page, ViewportSize, sync_playwright

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "user-manual"
VIEWPORT: ViewportSize = {"width": 1440, "height": 812}
AGENCY_ID = 1

# One label per interactive step, per locale -- each is a real accessible
# name or visible string read back from the i18n locale files (never a
# value derived from the current dataset, like a date range, which would
# stop matching the next time this script runs against fresher data).
LABELS = {
    "ja": {
        "hint": "ヒント",
        "on_time": "定時率",
        "agencies_tab": "事業者どうし",
        "you_badge": "あなた",
        "ask_card": "遅延の大きい路線：上位5件",
        "returned_data": "取得したデータ",
        "council_tab": "協議会向けまとめ・遅延証明",
        "certificate": "遅延証明書",
        "route_label": "路線",
        "route_placeholder": "路線を検索",
        "playback_toggle": "1日の動きを再生",
        "later": "後で",
    },
    "en": {
        "hint": "Hint",
        "on_time": "On-time rate",
        "agencies_tab": "Agencies",
        "you_badge": "YOU",
        "ask_card": "most delayed routes",
        "returned_data": "View returned data",
        "council_tab": "Council report",
        "certificate": "Delay certificate",
        "route_label": "Route",
        "route_placeholder": "Search routes",
        "playback_toggle": "Play the day",
        "later": "Later",
    },
}


def shot(page: Page, name: str, locale: str) -> None:
    path = OUT_DIR / f"{name}.{locale}.png"
    page.screenshot(path=str(path))
    print(f"wrote {path}")


def assert_dashboard(page: Page) -> None:
    """Fails loudly if the guest session landed on a login wall instead of
    the dashboard, rather than silently capturing and shipping that as a
    scene -- `login_required` left on is the one setup mistake that would
    otherwise corrupt every screenshot in the same way."""
    page.locator("[aria-label='Account menu'], [aria-label='アカウントメニュー']").first.wait_for(
        state="visible", timeout=15_000
    )


def first_route_code(page: Page, agency: str) -> str:
    """The top-ranked route on the delay-ranking report, read live off the
    page rather than a hardcoded code -- next time this runs, that week's
    top route will likely differ, and a code that no longer has any
    certificate-eligible departure would otherwise abort the whole run."""
    page.goto(f"{agency}/routes")
    page.wait_for_load_state("networkidle")
    row = page.locator("table tbody tr").first
    row.wait_for(timeout=15_000)
    text = row.inner_text()
    # Route rows read like "...中大野→野木 33101\xa0›\t平日\t...5.8..." -- the
    # route code is the run of digits immediately before the "›" link
    # marker, not the row's last number (later columns are stats, not the
    # code).
    match = re.search(r"(\d+)\s*\xa0?[››]", text)
    if not match:
        raise RuntimeError(f"could not read a route code from ranking row: {text!r}")
    return match.group(1)


def capture(base: str, locale: str) -> None:
    L = LABELS[locale]
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # 01: fresh context -- no agency chosen yet.
        ctx = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        ctx.add_init_script(
            f"window.localStorage.setItem('app.locale', '{locale}');"
            "window.localStorage.setItem('help_hint_dismissed', '1');"
        )
        page = ctx.new_page()
        page.goto(f"{base}/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        shot(page, "01-agency-select", locale)
        ctx.close()

        # Every other scene: agency already chosen via the URL.
        ctx = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        ctx.add_init_script(
            f"window.localStorage.setItem('app.locale', '{locale}');"
            "window.localStorage.setItem('help_hint_dismissed', '1');"
        )
        page = ctx.new_page()
        agency = f"{base}/agencies/{AGENCY_ID}"
        route_code = first_route_code(page, agency)

        page.goto(f"{agency}/pulse")
        page.wait_for_load_state("networkidle")
        assert_dashboard(page)
        page.wait_for_timeout(500)
        shot(page, "02-pulse", locale)

        # 03: scope sentence, period popover open. The period token is the
        # second `.scope-token` button in the sentence (route, period, dow,
        # time band, ...) -- located by that stable structural position,
        # never by its rendered text, which is the dataset's own date range
        # and moves every time this script's objective runs it again.
        page.locator(".scope-token").nth(1).click()
        page.wait_for_timeout(400)
        shot(page, "03-scope-sentence", locale)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        page.goto(f"{agency}/routes")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
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
        page.wait_for_timeout(500)
        shot(page, "08-routes-ontime", locale)

        page.goto(f"{agency}/time")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        shot(page, "07-time-trend", locale)

        page.goto(f"{agency}/why")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        shot(page, "09-why", locale)

        # 10: compare -> agencies tab, waiting for the ranked table (not
        # just the tab switch) to actually render.
        page.goto(f"{agency}/compare")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        page.get_by_text(L["agencies_tab"], exact=True).first.click()
        page.get_by_text(L["you_badge"], exact=True).first.wait_for(timeout=15_000)
        page.wait_for_timeout(300)
        shot(page, "10-compare-agencies", locale)

        # 11: live map. Prefer a populated replay of the latest observed
        # day over the live feed's own instant, which can be quiet at
        # capture time and would otherwise ship an empty map that neither
        # matches the manual's own description of it nor demonstrates
        # anything about how markers render.
        page.goto(f"{agency}/live")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        tour_dismiss = page.get_by_text(L["later"], exact=True)
        if tour_dismiss.count() > 0:
            tour_dismiss.first.click()
            page.wait_for_timeout(300)
        empty_state = page.locator(".ops-map__empty")
        if empty_state.count() > 0:
            empty_state.get_by_text(L["playback_toggle"], exact=False).first.click()
            page.wait_for_timeout(1500)
            # Jump the scrubber past the service day's start (where there is
            # rarely any service yet) to a mid-afternoon frame, instead of
            # waiting out real-time autoplay to get there.
            scrubber = page.locator("input.scrub-input")
            if scrubber.count() > 0:
                max_index = int(scrubber.get_attribute("max") or 0)
                if max_index > 0:
                    scrubber.fill(str(int(max_index * 0.65)))
                    page.wait_for_timeout(1500)
        shot(page, "11-live", locale)

        page.goto(f"{agency}/ask")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        shot(page, "12-ask-landing", locale)

        # 13: answer a ready-made question, waiting for the real result
        # (not just the loading state) before capturing.
        page.get_by_text(L["ask_card"], exact=False).first.click()
        page.get_by_text(L["returned_data"], exact=False).first.wait_for(timeout=20_000)
        page.wait_for_timeout(300)
        shot(page, "13-ask-answer", locale)

        # 15: the delay-certificate's full three-step flow -- the same
        # top-ranked route read off the ranking report earlier, then
        # whichever departure its own data returns.
        page.goto(f"{agency}/reports")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        page.get_by_text(L["council_tab"], exact=False).first.click()
        page.wait_for_timeout(300)
        page.get_by_text(L["certificate"], exact=True).first.click()
        page.wait_for_timeout(500)
        page.get_by_label(L["route_label"], exact=True).click()
        page.wait_for_timeout(300)
        page.get_by_placeholder(L["route_placeholder"], exact=False).fill(route_code)
        page.wait_for_timeout(500)
        option = page.get_by_role("option").first
        option.wait_for(timeout=15_000)
        option.click()
        page.wait_for_timeout(1200)
        select = page.locator("select").last
        select.wait_for(timeout=15_000)
        options = select.locator("option").all()
        if len(options) > 1:
            value = options[1].get_attribute("value")
            select.select_option(value=value)
        page.wait_for_timeout(500)
        shot(page, "15-reports-certificate", locale)

        ctx.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--locale", choices=["ja", "en", "both"], default="both")
    parser.add_argument("--base", default="http://localhost:8000")
    args = parser.parse_args()
    locales = ["ja", "en"] if args.locale == "both" else [args.locale]
    for locale in locales:
        capture(args.base, locale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
