# Reports tab

Printable period summary: a daily mean-delay trend chart with its spike
explained, the ten slowest routes over the shared filter range, one Export
menu (chart PNG, combined CSV, share link, browser print/PDF), and
browser-local saved-analysis bookmarks — plus a link out to Time for deeper
reports.

## How a user reaches it

- Route: Reports at `/agencies/:agencyId/reports`, rendered by
  `frontend/src/tabs/SavedExportTab.tsx` (`React.lazy`-loaded). The `doc`
  search param picks one of three views, and the screen's strip links to
  each:
  - no `doc` — the printable period summary (the default);
  - `doc=saved` — the saved analyses;
  - `doc=council` or `doc=certificate` — the export documents. A `report`
    param naming an export type (`council_summary`, `delay_certificate`)
    also opens this view.

  A leftover `view` param on this URL is replaced by its `doc`.
  `/agencies/:agencyId/saved` redirects here the same way (`savedTarget` in
  `frontend/src/routes/destinations.ts`): `view=saved` becomes `doc=saved`,
  `view=reports` becomes `doc=council`, and `report=<export type>` becomes
  its `doc`. `/agencies/:agencyId/reports/:reportType` goes to the screen
  that hosts the type (see `docs/features/analysis-tab.md`), or here with
  `doc=council` / `doc=certificate` for the two export types.
- Rail nav link: the Reports entry, last in
  `frontend/src/components/sidebarNavItems.ts`'s `SIDEBAR_NAV_ITEMS`
  (`nav.reports` — "Reports" / "レポート"). On a phone it sits in the More
  sheet.
- The period summary and `doc=saved` views render
  `frontend/src/tabs/ReportsHomeTab.tsx`, which reads the same `doc` param;
  the export documents render `AnalysisTab` with the two export types,
  opening `delay_certificate` for `doc=certificate` and `council_summary`
  otherwise.
- Users see `delay_certificate` as 「遅延の記録（参考）」 / "Delay record (for
  reference)". It is the service's reference document built from the
  operator's real-time feed, not a certificate the operator issues, so its
  copy, CSV file name and footnotes never call it a certificate, and the
  printable card carries a disclaimer naming the operator. The type id, the
  `doc=certificate` param and the API keep the old name so links and saved
  views keep working. `tests/frontend/i18n_glossary.test.mjs` holds the copy
  to that name.
- The delay record is three numbered steps
  (`frontend/src/components/analysis/DelayCertificateLookup.tsx`): the day,
  which stops at the agency's last data day (a typed later day is pulled back
  to it), the route, then the departure among that day's late ones, each
  shown as 「17:42 広島駅 発（8分遅れ）」. The record's sentence names the stop
  its times were taken at. That stop is the earliest one the feed recorded
  for the trip; a feed often sends nothing for a trip's first stop, so each
  row also says whether the loaded timetable starts the trip there
  (`compute_delay_certificate`'s `stop_name`/`stop_is_first`, looked up in
  `static_stop_times`/`static_stops`). Only then does the record call it the
  trip's start (「始発側の「広島駅」での発車時刻です。」); otherwise it says
  the stop is not where the trip starts, and with no name in the timetable it
  speaks of the first recorded stop. It never names the stop the passenger
  boarded at. Only the loaded timetable is kept and a reload replaces it in
  place, so a stop is named only when the loaded timetable runs the trip on
  the record's day (`plan_ctes`' running rule, the one planned trips count
  by): a record from before it starts, or a trip_id it reuses under a service
  not running that day, falls back to the first recorded stop.
- Staff open 「遅れた便の一覧」 under the lookup: every route's late
  departures (the report's default threshold) on the day the lookup shows,
  fetched only once the section is opened, with that day's CSV. The record's
  day and route are its whole scope, so this document has no scope sentence.
- The council document's planned trips, executed trips and service-delivered
  rate count only the days a timetable plans: a day's count saved in
  `planned_trips_daily` while its timetable was loaded, or else the loaded
  timetable's plan for it (`pipeline/planned_trips.py`). When those days fall
  short of the period, the planned-trips tile says 「※時刻表に計画がある16/30日分」
  from the response's `covered_days`/`window_days`, the same wording as the
  agencies board, and a footnote repeats it. Days from before the first save
  stay uncounted: the reload that replaced their timetable had already erased
  their plan.
- The council document's tiles are 定時率, 平均遅延, 観測データ数（停留所単位）
  (one reading per trip per stop, so far larger than the planned trips), 計画便数
  and 運行率 (`frontend/src/components/analysis/CouncilSummaryBlock.tsx`). A
  missing 運行率 shows 「—」 with the response's `delivered_missing` reason in
  the agencies board's words (`network.delivered_missing.*`), and the server's
  footnote (`format_council_summary_footnotes`) gives the same reason. The
  on-time definition footnote states the tolerance in words, 「定時の定義：定刻
  から1分以内の遅れ（早発も定時として数えます）」, not its preset name; the CSV
  preamble keeps the preset for comparing exports. Each figure keeps to one
  line (`white-space: nowrap`), so a phone never breaks a number in two.
- The document is called 協議会向け報告 / "Council report" wherever it is
  named: its tab, its subtitle, the report list and the printed header.

What the user sees/does:

- **Summary view** (default):
  - An **Export menu** in the header (`frontend/src/components/ExportMenu.tsx`)
    holding the page-level actions: a PNG of the trend chart, a combined CSV
    (trend + ranking rows), a "Create share link" item that copies the
    current URL with its filter query string, and "Print / Save as PDF"
    (`window.print()`).
  - **Scope sentence** — `frontend/src/components/scope/ScopeSentence.tsx`,
    greying what the trend report's `scope_applied` did not use.
  - **Trend section** — a daily mean-delay line chart
    (`frontend/src/components/analysis/PeriodChart.tsx`). Under it, the day
    whose mean rose furthest above its 7-day moving average
    (`avg_min_smoothed`), with that day's `top_offenders`: the spike a
    reader of the chart asks about. No day is named when none rose above it.
  - **Routes-to-check section** — the ten slowest patterns (pattern, mean
    delay, samples), each row's "Open analysis →" link opening that route's
    dossier (`/agencies/:agencyId/routes/<route_code>`) with the active
    scope's query string, `service` included when one is set. When there are
    more, a line says how many of how many are shown and links every route
    on the Routes table, so the page stays one printable sheet.
  - The Export menu is the page's only export; its CSV still holds every
    ranked route the API returned.
  - A `DefinitionMetaBlock`, its own collapsed disclosure (on-time/late
    tolerance, measurement point, dedup rule, exclusion threshold) from the
    trend report's `definition` field, then a **definitions** `<details>`
    showing the active filter window and a **"Detailed reports →"** link to
    Time's `trend` report (`/agencies/:agencyId/time?report=trend`) carrying
    the same filter.
- **Saved view** (`?doc=saved`) — lists this browser's saved analyses for
  the current agency (`frontend/src/components/analysis/savedAnalyses.ts`),
  each opening with its saved filter query through `routesHref`: one saved
  route opens that route's dossier, any other selection the Routes list. An
  `EmptyState` ("Save filters on a route's page to see them here") shows
  when none exist for this agency. A note under the local-only one tells
  these 「保存した分析」 apart from 「保存したビュー」, the account-held views
  kept under the scope sentence's "Saved views ▾", which this tab does not
  list; the names are the privacy policy's. Bookmarks are
  browser-local filter snapshots, not immutable historical results —
  opening one re-queries the API with today's data under that filter.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useReport(agencyId, "trend", ctx)` | `GET /api/{agency_id}/reports/trend` (`api/routers/reports.py: get_report`) | `pipeline/reports/rankings.py: compute_trend_series()` — precomputed `agg_daily_trend` by default, falling back to a live ClickHouse scan for a `time_band`-narrowed request. Same function the Analysis tab's `trend` report and the Ask tab's `trend` tool call. |
| `useReport(agencyId, "ranking", ctx)` | `GET /api/{agency_id}/reports/ranking` (`api/routers/reports.py: get_report`) | `pipeline/reports/rankings.py: compute_ranking()` — same `agg_*`-fast-path-with-live-fallback pattern as `trend`. Takes the endpoint's default observation floor (`reliable_min_samples` in the response), which the section states under its table; the Routes screen's ranking is where thinly observed routes can be included. |
| `useAgencies()` (only used for the current agency's display name in the section heading) | `GET /api/agencies` (`api/routers/agencies.py`, not scoped under `/api/{agency_id}`) | Static agency metadata from Postgres. |

Saved-analysis reads/writes and the CSV/PNG/share-link/print actions are
entirely client-side — they read `trend`/`ranking` data already in memory rather
than issuing their own requests.

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/SavedExportTab.tsx` | Reports shell: the `doc` strip, the `view` → `doc` replacement, and which screen each document renders |
| `frontend/src/tabs/ReportsHomeTab.tsx` | Period summary and saved views: trend with its spike, the ten slowest routes, saved-analysis list |
| `frontend/src/components/ExportMenu.tsx` | The header Export menu: chart PNG, combined CSV, share link, print |
| `frontend/src/routes/destinations.ts` | `savedTarget` (`view` → `doc`), and the `routeHref`/`routesHref`/`reportHref` links out of this screen |
| `frontend/src/components/analysis/PeriodChart.tsx` | Daily mean-delay trend chart |
| `frontend/src/components/analysis/savedAnalyses.ts` | Browser-local saved-analysis read/write/delete |
| `frontend/src/components/analysis/csv.ts` | `downloadCsv()`, the browser-side CSV download this screen shares with the route dossier, Live and Ask (the `AnalysisTab` screens link to the API's `?format=csv` instead) |
| `frontend/src/components/DefinitionMetaBlock.tsx` | Renders the on-time/late definition metadata block |
| `frontend/src/components/scope/ScopeSentence.tsx` | The scope sentence, its popovers and pinned strip |
| `frontend/src/api/hooks.ts` | `useReport`, `useAgencies` |

**Backend**

| File | Role |
|---|---|
| `api/routers/reports.py` | `GET /reports/{report_type}` (shared with the Analysis tab) |
| `pipeline/reports/rankings.py` | `compute_trend_series()`, `compute_ranking()` |
| `pipeline/reports/definition.py` | Definition metadata resolved into every report's `definition` field |
| `pipeline/analyze.py` | Builds `agg_daily_trend` and the ranking aggregates this tab reads |

## How to verify manually

**Automated tests:**

- Frontend: `frontend/src/components/analysis/workflows.test.tsx` (renders
  `ReportsHomeTab` and `RouteAnalysisTab` together on their real routes,
  including a saved analysis opening its route's dossier),
  `frontend/src/tabs/ReportsHomeTab.test.tsx` (the routes-to-check →
  dossier and "Detailed reports" → Time links),
  `frontend/src/tabs/SavedExportTab.test.tsx` (the `doc` views and the
  `view` → `doc` replacement).
- Backend: `tests/api/test_reports.py`, `tests/unit/test_reports_rounding.py`
  (both shared with the Analysis tab, since both read the same
  `/reports/{report_type}` endpoint).

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load and analyze
   data first: `make fetch-ingest` (or `make load_static` before `ingest_live`),
   then `make analyze` for the agency.
2. Click "Reports" in the rail → URL `/agencies/:agencyId/reports`;
   expect the trend chart and routes-to-check table to populate (or the
   empty state with no data).
3. Change the scope sentence's period / days / time / routes —
   expect both sections to refetch.
4. Click a row's "Open analysis →" link in the routes-to-check table —
   expect navigation to that route's dossier,
   `/agencies/:agencyId/routes/<route_code>`, carrying the same filter.
5. Click "Detailed reports →" — expect navigation to
   `/agencies/:agencyId/time?report=trend` carrying the same filter.
6. Confirm the sections carry no download buttons of their own; open the
   header's "Export" menu and choose "Download CSV" — expect one combined
   file holding every ranked route, and "Download PNG" — expect an image of
   the trend chart.
7. In the "Export" menu, choose "Create share link" — expect a
   clipboard-copy confirmation; paste the URL in a new tab and confirm the
   same filter loads.
8. In the "Export" menu, choose "Print / Save as PDF" — expect the browser
   print dialog.
9. Switch to the "Saved analyses" view → URL
   `/agencies/:agencyId/reports?doc=saved`; expect any bookmarks saved from
   a route's dossier for this agency to appear, each linking back to that
   dossier with its saved filter; delete one and confirm it disappears.

## i18n

- The period summary's and saved view's strings come from the `design` i18n
  namespace (`frontend/src/i18n/design.ts`, loaded via
  `useTranslation("design")`); the screen's heading and `doc` strip use
  `saved.*` in `frontend/src/i18n/locales/{ja,en}.json`.
  `docs/features/route-analysis-tab.md`'s i18n section describes how its
  `ja`/`en` key parity and wording are checked.
- Every report response carries the same `definition` block described in
  `docs/features/analysis-tab.md`'s i18n section; this tab renders it through
  the shared `DefinitionMetaBlock` component rather than duplicating that
  logic.
