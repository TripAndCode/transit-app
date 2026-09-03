"""Frontend dashboard display check against item 21's synthetic ground truth.

Item 21 (`tests/pipeline/test_synthetic_agg_e2e.py`) proved `pipeline/analyze.py`
builds numerically-correct `agg_*` rows from a hand-computable synthetic
GTFS static+RT fixture. It never rendered anything — a request/response or
frontend-formatting bug downstream of `agg_*` (a unit mix-up, an off-by-one
in a query filter, a component reading the wrong field) could still show a
wrong number on screen while every `agg_*` row is correct. This module
closes that specific gap for the Overview and Analysis/ranking tabs (the two
most numeric-heavy report surfaces — see `docs/features/overview-tab.md` and
`docs/features/analysis-tab.md`): it seeds one dedicated agency per item 21
pattern into the throwaway Postgres (`:5544`)/ClickHouse (`:8124`) stack,
runs `analyze()`, launches the real FastAPI app (serving the built SPA) as a
subprocess, and uses Playwright to load the actual Overview and Analysis
ranking pages and scrape the on-screen numbers — asserting them against the
SAME `pattern.expected["agg_route_stats"]` dict item 21 itself imports
(`tests.fixtures.synthetic_gtfs`), never a second hand-copied/re-derived
value.

One dedicated agency per pattern (not one shared agency, and not the
`agency_id` fixture from `tests/conftest.py`, which only makes one):
`pipeline.static_loader.load_static` DELETEs an agency's existing static
rows before reloading (see its own source, `pipeline/static_loader.py`
line ~81), so calling it three times against one shared `agency_id` would
silently wipe each earlier pattern's routes/stops/trips, leaving only the
last pattern loaded. A dedicated agency per pattern also means every number
this test checks — the Overview headline's agency-wide `avg_min`
(`OverviewHeroRow.tsx`), and the Analysis ranking table's per-route
`avg_min`/`samples` (`ReportTable.tsx`) — is exactly that ONE pattern's
`agg_route_stats` value, with no need to hand-derive a second, cross-route
combined expectation this test and item 21 could then silently disagree
about.

Why Overview's "Routes to check" list and headline DELTA are NOT the check
target here (both considered and rejected while writing this test):
- "Routes to check" (`RoutesToCheckList.tsx`) excludes the "ok" severity
  band (< 1.5 min, `frontend/src/styles/tokens.ts`'s `delayBand`) from its
  rendered groups entirely (`routesToCheckBands.ts`'s `groupBySeverityBand`)
  — ALL THREE of item 21's patterns (0.5 / 0.88 / 1.0 min) fall under 1.5,
  so this list always renders empty for this fixture regardless of
  correctness. Not a usable check target.
- The headline's delta-vs-prior-week (`hasBaseline` in `OverviewHeroRow.tsx`)
  needs a second 7-day window of data this single-day fixture doesn't have,
  so it always renders "no baseline" — also not usable. The headline's raw
  `avg_min` value (`ov-kpi-value`, rendered regardless of baseline) IS used
  below: with `from=to=<the fixture's one date>`, `compute_overview_summary`
  anchors its current-window date range at the latest data date
  (`pipeline/reports/overview.py`'s `anchor`/`cur_from`/`cur_to`), which
  collapses to exactly that one day for a freshly-seeded, single-day agency
  — so `_headline_stats` (`SUM(avg_min*samples)/SUM(samples)` over
  `agg_daily_trend`) reduces to that agency's one route's own `avg_min`.

Why the Analysis ranking table's median/p90 columns are NOT checked here
(also considered and rejected): `pipeline/reports/rankings.py`'s
`compute_ranking` fast path reads `agg_route_daily_dist`'s per-day delay
HISTOGRAM and interpolates p50/p90 from it (`percentile_from_hist`) — a
different algorithm from `agg_route_stats`'s `PERCENTILE_DISC()`-based
formula item 21 hand-verified (see that function's own docstring). An
interpolating histogram estimate and an exact discrete percentile over the
same underlying data are not guaranteed to agree bucket-for-bucket —
asserting item 21's `p50_min`/`p90_min` against the ranking table's columns
would be checking a real, intentional methodological difference, not a
rendering bug. Only `avg_min` (plain `SUM(sum_delay_sec)/SUM(samples)`,
identical math on both tables) and `samples` (an exact count, identical on
both tables) are checked.

Skips by default (needs `RUN_CH_INTEGRATION=1`, a built SPA, and a real
Chromium — the same tier as `tests/i18n_coverage_test.py`, which this
module's `app_server`/`_free_port` fixtures are adapted from):

    cd frontend && npm run build   # api/static/index.html must exist
    DATABASE_URL=postgresql://transit:transit@localhost:5544/transit_test \\
      RUN_CH_INTEGRATION=1 RUN_DASHBOARD_E2E_SCAN=1 \\
      CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=8124 \\
      CLICKHOUSE_USER=transit CLICKHOUSE_PASSWORD=transit CLICKHOUSE_DATABASE=transit_test \\
      poetry run pytest tests/dashboard_synthetic_display_test.py -v

Verify item 22's own "not a vacuous pass" requirement by temporarily
swapping/corrupting one delay value in one of
`tests.fixtures.synthetic_gtfs`'s pattern builders (or monkeypatching one
`expected["agg_route_stats"]["avg_min"]` in a scratch copy) and confirming
this test goes red, then reverting and confirming it's green again — see
`tests/unit/test_dashboard_value_check.py` for a fast, offline, always-run
version of the same corruption check against the pure comparison helper.

Provisioning history, kept for context: the implementing session could not
launch this test at all (no `poetry install`/`npm install` in its Bash
allowlist, and `frontend/node_modules` was missing the `mermaid` package),
so every selector/query-param/column-index/rounding rule above was traced by
hand against source instead. A later, fully-provisioned interactive session
closed that gap for real: built the SPA, installed Playwright's Chromium,
and ran the command block above against the live throwaway Postgres/
ClickHouse stack — both tests passed, and the corruption check above was
performed for real (temporarily forced `uniform_delays`'s `avg_min` from 0.5
to 99.9 in `tests/fixtures/synthetic_gtfs.py`, confirmed both tests failed
with a clear mismatch message, then reverted and reconfirmed green). See
`docs/refactor-log.md` for the full command line and output summary.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from tests.fixtures.dashboard_value_check import assert_avg_min_matches, assert_samples_matches
from tests.fixtures.synthetic_gtfs import ALL_PATTERNS, SyntheticPattern, run_pattern

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DASHBOARD_E2E_SCAN") != "1",
    reason="set RUN_DASHBOARD_E2E_SCAN=1 (+ RUN_CH_INTEGRATION=1, see transit-app-gotchas) to run "
    "— seeds the throwaway DB, builds/serves the real SPA, and drives a real Chromium",
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pytest.skip(
        "playwright not installed; run `poetry add --group dev playwright` and `playwright install chromium`",
        allow_module_level=True,
    )

_STATIC_INDEX = Path("api/static/index.html")


def _free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def seeded_agencies(tmp_path, pg_conn, ch_client) -> dict[str, tuple[int, SyntheticPattern]]:
    """Seed one dedicated agency per item-21 pattern; return name -> (agency_id, pattern)."""
    out: dict[str, tuple[int, SyntheticPattern]] = {}
    for pattern_fn in ALL_PATTERNS:
        pattern = pattern_fn()
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agencies (agency_name, feed_url) VALUES (%s, %s) RETURNING agency_id",
                (f"item22-{pattern.name}", f"http://example.com/item22-{pattern.name}.pb"),
            )
            agency_id = cur.fetchone()[0]
        pg_conn.commit()
        run_pattern(pattern, tmp_path, pg_conn, agency_id, ch_client)
        out[pattern.name] = (agency_id, pattern)
    return out


@pytest.fixture(scope="module")
def app_server():
    """Launch uvicorn on a free port against the throwaway test DB env already
    resolved by `tests/conftest.py` (DATABASE_URL redirected to `_test`,
    CLICKHOUSE_* read straight from the environment — see this module's
    docstring for the full required env block). Requires
    `api/static/index.html` to exist (build the SPA first); skips with a
    clear message if not. Adapted from `tests/i18n_coverage_test.py`'s
    identical-shape fixture, including its `scope="module"` — this module's
    two test functions don't need a fresh server per test (neither depends
    on any per-test DB-isolation fixture), so module scope halves the
    subprocess boot/health-poll/teardown cost instead of paying it twice.
    """
    static_index = Path(__file__).parent.parent / _STATIC_INDEX
    if not static_index.exists():
        pytest.skip(f"SPA not built — {_STATIC_INDEX} is missing. Run `cd frontend && npm run build` first.")

    port = _free_port()
    proc = subprocess.Popen(
        ["poetry", "run", "uvicorn", "api.main:app", "--port", str(port), "--no-access-log"],
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 30
    started = False
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            started = True
            break
        except Exception:
            time.sleep(0.5)

    if not started:
        proc.kill()
        pytest.fail("API server did not start within 30 seconds.")

    yield f"http://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def browser():
    """One Chromium instance shared by both test functions in this module —
    each still gets its own fresh `page` (see each test body), so there's no
    cross-test state to isolate, just an avoidable second launch/teardown of
    an already-heavy (real browser + real DB + real subprocess) test tier."""
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


def test_overview_headline_matches_synthetic_ground_truth(seeded_agencies, app_server, browser):
    """Overview hero row's avg-delay tile (`.ov-kpi-value`) for each pattern's
    dedicated, single-route agency must match that pattern's
    `agg_route_stats.avg_min` (rounded to 1dp for display)."""
    base = app_server
    page = browser.new_page()
    try:
        for name, (agency_id, pattern) in seeded_agencies.items():
            url = f"{base}/agencies/{agency_id}/overview?from={pattern.date}&to={pattern.date}"
            page.goto(url, wait_until="networkidle", timeout=30_000)
            page.wait_for_selector(".ov-kpi-row .ov-kpi-tile", timeout=15_000)
            cell_text = page.locator(".ov-kpi-row .ov-kpi-tile").first.locator(".ov-kpi-value").inner_text()
            assert_avg_min_matches(
                cell_text,
                pattern.expected["agg_route_stats"]["avg_min"],
                label=f"overview headline / {name}",
            )
    finally:
        page.close()


def test_analysis_ranking_table_matches_synthetic_ground_truth(seeded_agencies, app_server, browser):
    """Analysis tab's `ranking` report table — each pattern's dedicated
    agency has exactly one route, so the single body row's avg (td index 3)
    and samples (td index 6) columns must match `agg_route_stats` exactly
    (see module docstring for why median/p90, td indices 4/5, are
    deliberately NOT checked here)."""
    base = app_server
    page = browser.new_page()
    try:
        for name, (agency_id, pattern) in seeded_agencies.items():
            url = f"{base}/agencies/{agency_id}/analysis/ranking?from={pattern.date}&to={pattern.date}"
            page.goto(url, wait_until="networkidle", timeout=30_000)
            page.wait_for_selector("table tbody tr", timeout=15_000)
            rows = page.locator("table tbody tr")
            assert rows.count() == 1, (
                f"{name}: expected exactly 1 ranking row (one route in this dedicated agency), "
                f"got {rows.count()} — either the route was filtered out (e.g. the ranking fast "
                f"path's `samples > 20` gate) or a stale/leftover row leaked in"
            )
            cells = rows.first.locator("td").all_inner_texts()
            exp = pattern.expected["agg_route_stats"]
            assert_avg_min_matches(cells[3], exp["avg_min"], label=f"analysis ranking avg / {name}")
            assert_samples_matches(cells[6], exp["samples"], label=f"analysis ranking samples / {name}")
    finally:
        page.close()
