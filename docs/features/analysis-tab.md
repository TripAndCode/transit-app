# Analysis tab

Dense, desktop-oriented report browser: a list of live-queried report types
(ranking, on-time rate, trend, dow comparisons, ...) plus a route-forecast
section, each scoped to the shared date/dow/time-band/service/route filter,
with a proactive "Insight Panel" suggesting what to look at next.

## How a user reaches it

- Hosted by four of the rail's destinations. Each passes AnalysisTab the
  report types it hosts (the per-screen lists in
  `frontend/src/routes/destinations.ts`):
  - Routes (`/agencies/:agencyId/routes`, `frontend/src/tabs/RoutesIndex.tsx`)
    — `ranking`, `ranking_best`, `on_time`, `worst_5min`, with
    `HeadwayQualityPanel` and `PerformanceStandardPanel` beside `on_time`.
    A route opener (a `<select>` and an Open button) above the reports opens
    a route's dossier, and
    `?sort=<report type>` picks which report opens when no `report` param is
    set;
  - Time (`/agencies/:agencyId/time`, `frontend/src/tabs/TimeTab.tsx`) —
    `trend`, `dow_weekday`, `dow_weekend`, `route_forecast`;
  - Why (`/agencies/:agencyId/why`, `frontend/src/tabs/WhyTab.tsx`) —
    `dwell_run`, with the rain (`WeatherDelayPanel`) and long-gap
    (`HeadwayQualityPanel`) panels beside it;
  - Compare (`/agencies/:agencyId/compare`, `frontend/src/tabs/CompareTab.tsx`)
    for any `by` other than `agencies` (default `periods`) —
    `compare_ranking`.

  Reports hosts it too, for the export-style types `council_summary` and
  `delay_certificate` (`/agencies/:agencyId/reports?doc=council` or
  `?doc=certificate`, see `docs/features/reports-tab.md`).
- Redirects keep the query string: `/agencies/:agencyId/analysis/:reportType`
  and `/agencies/:agencyId/reports/:reportType` send a report-type segment to
  the screen hosting it (with `report=<type>` on Routes, Time, Why or Compare,
  and `doc=council` / `doc=certificate` on Reports). The named `analysis/`
  segments go to their screens: `when` to Time, `why` to Why, `rider` to
  `routes?sort=on_time`, `compare` to `compare?by=periods` (`by=agencies`
  when `mode=agencies`), `overview` and `predict` to Pulse, and `where` as
  described in `docs/features/route-analysis-tab.md`. Any other
  `analysis/` or `reports/` segment goes to Pulse.
  `/agencies/:agencyId/forecast` goes to `time?report=route_forecast`
  (`frontend/src/routes/legacyRedirects.tsx`, through `reportHref` in
  `destinations.ts`).
- Reach it from the Routes, Time, Why and Compare rail entries, the command
  palette's report items, the Insight Panel's suggestion (both through
  `reportHref`, which opens the screen hosting the type), or the Reports
  period summary's "Detailed reports →" link (`time?report=trend`).
- Top-level component: `frontend/src/tabs/AnalysisTab.tsx`, which takes
  `reportTypes` and an optional `defaultReport`, and reads the selected one
  from the `report` search param: the param when the screen hosts that type,
  else `defaultReport` when the screen hosts it, else the screen's first
  type. It composes the report list, the selected report's body, and the
  Insight Panel.

What the user sees/does:

- **Scope sentence** — `frontend/src/components/scope/ScopeSentence.tsx`
  (see Scope below), greying what the open report's `scope_applied` did not
  use.
- **Report list** (left column) — one button per report type the hosting
  screen owns; clicking sets `report={reportType}` on the current URL, keeping
  the filter query string. A `?` hint icon (`frontend/src/components/InsightHint.tsx`)
  explains what each report type means.
- **Report body** (center column) — for most report types,
  `frontend/src/components/ReportTable.tsx` renders the rows, with a CSV
  download link and a raw-rows JSON `<details>` dump; `trend` instead
  renders `TrendBlock` (defined inline in `AnalysisTab.tsx`): a
  day-of-week x time-band heatmap
  (`frontend/src/components/charts/DowBandGrid.tsx`), a daily line chart
  (`frontend/src/components/charts/DailyChart.tsx`), and an hourly heatmap
  (`frontend/src/components/charts/HourlyHeatmap.tsx`). An empty result
  shows `EmptyState` with a "reset to this week" recovery action. `dwell_run`
  instead renders `DwellRunBlock` (defined inline in `AnalysisTab.tsx`): a
  per-route dwell-time/running-time table decomposing `arr_delay` (populated
  only for `ingest_strategy == 'static_join'` feeds) and `dep_delay` separately
  from the existing delay histogram — an explicit `EmptyState` renders
  instead when the agency's feed never sends `arr_delay` (`available:
  false`) or the current time-band filter isn't servable by this
  decomposition (`time_band_supported: false`), never a misleading zero.
  `route_forecast` instead renders `frontend/src/components/RouteForecastSection.tsx`
  (agency-wide landing view, or a per-route detail view when exactly one
  route is selected in the shared filter - see its own file-header comment).
- **Insight Panel** (right column) —
  `frontend/src/components/InsightPanel.tsx`: a single proactive suggestion
  ("this route's trend just shifted", "on-time rate dropped", etc.),
  dismissible per-suggestion (persisted to `sessionStorage`, keyed by
  agency) and toggleable off entirely; defaults on in dev builds, opt-in in
  production (same `import.meta.env.DEV` default pattern as the rail's
  prototype section).
- Below ~640px (`MOBILE_BREAKPOINT_PX`,
  `frontend/src/hooks/useMediaQuery.ts`) the three columns stack vertically
  instead of side-by-side — this tab otherwise stays desktop-oriented by
  design (see the inline comment in `AnalysisTab.tsx`).

## Scope

Every analysis screen reads and writes one shared scope in the URL through
`frontend/src/api/scope.ts` (`useScope`, `parseScope`, `scopeToQueryString`,
`applyScopePatch`). The backend validates the same parameters in
`api/range.py`'s `clamp_range_ctx`.

| Param | Values |
|---|---|
| `from`, `to` | ISO dates |
| `dow` | `all`, `weekday`, `weekend`, or a comma list of `mon`…`sun` |
| `time_band` | the seven bands, or `all` |
| `hour` | `0`–`23`, or an inclusive range `a-b` |
| `service` | `all`, `平日`, `土日祝` |
| `routes` | comma list, at most 100 |
| `stop` | a GTFS `stop_id` |
| `dir` | `0` or `1` (`direction_id`) |
| `late`, `early` | on-time tolerance in seconds, `0`–`3600` |

Rules the two sides share:

- **`dow` is canonical.** A list is de-duplicated and put in Monday-first
  order. A list that names exactly a legacy group becomes that group:
  `mon,…,fri` is `weekday`, `sat,sun` is `weekend`, and all seven days are
  `all`.
- **`hour` excludes `time_band`.** In the browser, `hour` wins and
  `time_band` is dropped. The API answers 422 when a request carries both.
- **Invalid values.** An invalid value in the page URL falls back to that
  condition's default instead of reaching the API. The API itself answers
  422 for an invalid value, as it does for the older fields.
- **Unknown params survive.** `scope.ts` writes only its own params, so
  screen-local ones survive a scope change: `report`, `sort`, `by`, `doc`,
  `sub_tab`, and the route dossier's `compare=1` (the week-earlier overlay).
  An agency switch keeps the destination plus `by` and `doc`
  (`agencySwitchHref` in `destinations.ts`).
- **Each screen keeps its own scope.** The rail, the phone tab bar, the More
  sheet and the palette open a screen with the scope that screen last showed
  for the agency, never the scope of the screen being left
  (`frontend/src/api/screenScope.ts`). The record lives in sessionStorage, so
  a new tab opens every screen on its defaults, and it holds only the params
  a URL states outright, so the default period keeps rolling. Links whose
  job is to carry the current scope elsewhere (Worth a look, saved views)
  build their own query. The signed-out restore on a fresh visit
  (`anonymousFilterPersistence.ts`) is keyed per screen the same way.

Every endpoint behind these screens returns `scope_applied`: one boolean per
field in `api/scope_applied.py`'s `SCOPE_FIELDS`, saying whether this
response honoured that field.

- **Where it is declared.** The honoured sets sit beside each endpoint.
  Per-report sets are `_REPORT_HONOURS` in `api/routers/reports.py`, and the
  panel endpoints' sets sit beside those; overview, network and map declare
  theirs in their own routers.
- **`late`/`early`.** They are the on-time tolerance, applied by `on_time`
  and `council_summary`. Elsewhere they are ignored and reported as `false`,
  including `worst_5min`, whose over-5-min threshold stays its own
  `late_tolerance_sec`.
- **Not yet honoured.** `hour`, `stop` and `dir` are accepted and validated,
  but no endpoint honours them yet: `hour` waits on `agg_route_hour_daily`,
  and none of today's aggregates carries `direction_id`.

### The scope sentence

Pulse, Routes, Time, Why, Compare by periods and the Reports summary state
the scope as one sentence above their content
(`frontend/src/components/scope/ScopeSentence.tsx`), for example
「青森市バスの全路線を、9/1〜9/28のすべての曜日・終日で、定時は1分以内として見る」.

- **Words.** `scopeTokens` in `components/scope/scopePhrases.ts` turns a
  scope into one labelled token per condition, in sentence order, and names
  the `scope_applied` field each maps to. The locale template
  `scope.sentence` places them with `[slot]` placeholders. Timetable, stop,
  direction and the early tolerance appear only when set, in `[extras]`. A line picked by name reads as that
  line with its variant count, not as a route count. `scopeTitle` reuses
  the same words for saved-analysis titles. Punctuation after a token stays
  on its line.
- **Popovers.** Every condition but the agency is a button that opens its
  own control in a `role="dialog"` popover (`ScopePopover.tsx`). Focus moves
  in. Escape closes it and returns focus to the button, through the shared
  Escape stack (`useTopmostEscape`), so an overlay opened over it takes the
  Escape instead. A click outside, or Tab walking off its last control,
  also closes it. It is positioned against the sentence's section and slid
  left to fit, so it never runs off a phone screen. The controls are in `scopeControls.tsx`:
  - Period: presets ending on the agency's latest data day (see the
    controls' data below), and from/to dates (a start after the end is
    ignored).
  - Days: all, weekdays or weekend, seven weekday toggles that never
    remove the last day, and the timetable select.
  - Time: the seven bands and all day. Hour-by-hour filtering is shown as
    pending until `agg_route_hour_daily` exists.
  - Routes: the grouped route picker.
  - On-time tolerance: 1/3/5 minutes and a slider that writes when the drag
    ends.
- **The controls' data.** Once a popover opens or the strip is pinned,
  `useScopeSummary` reads `GET /api/{agency_id}/scope/summary`
  (`api/routers/scope_summary.py`, computed by
  `pipeline/reports/scope_summary.py` from `agg_route_daily_dist` alone):
  - the period control draws the last 90 days of data as bars coloured by
    the delay ramp (`delayRampVar`, `--d0`…`--d4`) with a legend, each bar
    naming its date and mean, and days without data drawn as short marks.
    The period is outlined and the days outside it dimmed. A brush over the
    bars (`PeriodBrush.tsx`) selects a range by dragging across them, or
    moves one edge by dragging its handle; from the keyboard each handle
    moves a day at a time (Home/End, Page keys for a week). It writes when
    the drag, the key or the focus ends, and only the edge that moved, so an
    edge outside the window keeps its date. A tap, a cancelled drag or a
    non-primary button writes nothing. Presets are the last 7, 14 and 30 days and
    「収集開始から」 (from the earliest data day, at most 365 days back);
  - each weekday toggle shows that weekday's mean delay over the period;
  - each line in the route picker shows its mean delay over the period;
  - the tolerance control draws the on-time share at each late tolerance
    (0–10 min) with a marker and readout at the current one, and names the
    share on each preset. The one-minute step is the exact on-time count,
    matching the screens; the other steps are histogram estimates.

  The bars ignore the period and the weekday filter, the weekday means
  ignore the weekday filter, and the route means ignore the routes filter,
  since each exists to choose that condition. The aggregate has no hour of
  day, stop or direction, so with one of those set the controls say their
  figures cover the whole day (and every stop and direction). The request
  carries only the fields the summary answers to (`scopeSummaryQuery`), and
  only the controls that draw its figures fetch it.
- **Live apply.** Every change writes the URL at once with `replace`;
  there is no Apply button.
- **Greying.** Each screen passes its main response's `scope_applied`. A
  set condition whose field is `false` renders struck through, in the
  sentence and in the pinned strip, with 「この画面では使われない条件です」
  as its title and accessible description. A condition at its default
  filters nothing and is never greyed. A screen that passes nothing greys
  nothing. AnalysisTab uses the forecast overview's map for
  `route_forecast`, and a report's map only once that report's response is
  the one on screen.
- **Pinned strip.** 「帯を固定」 shows every control inline under the
  sentence; the choice is kept in localStorage `transit.scopePinned`.
- **Presets and reset.** Signed-in users save and load presets beside the
  sentence (`PresetMenu`). 「条件をリセット」 appears once any non-date
  condition differs from the default, and resets the dates too.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useScopeSummary(agencyId, scope, enabled)` | `GET /api/{agency_id}/scope/summary` (`api/routers/scope_summary.py: scope_summary`) | `pipeline/reports/scope_summary.py: compute_scope_summary()` — four reads of `agg_route_daily_dist` (span, daily, per-weekday, per-route) plus one merged histogram for the tolerance curve; no ClickHouse. |
| `useReports(agencyId)` | `GET /api/{agency_id}/reports` (`api/routers/reports.py: list_reports`) | Static metadata only — the fixed `_REPORT_TYPES` tuple, no DB read. |
| `useReport(agencyId, reportType, ctx, options)` | `GET /api/{agency_id}/reports/{report_type}` (`api/routers/reports.py: get_report`) | Computed live per request from `pipeline/reports/rankings.py`'s `compute_ranking` / `compute_dow_ranking` / `compute_on_time` / `compute_worst_5min` / `compute_trend_series` / `compute_compare_ranking` / `compute_hourly_heatmap` — each follows the repo-wide pattern of a precomputed-`agg_*` fast path with a live ClickHouse fallback for a `time_band`-narrowed request (see `AGENTS.md` and the `ask-tab.md` doc's "ranking family" note — these are the same functions the Ask tab's `top_n`/`on_time`/`trend`/`cmp_service` tools call). `dwell_run` instead reads `pipeline/reports/dwell_run.py`'s `compute_dwell_run_decomposition` from `agg_route_daily_dwell_run` — no live fallback (a time-band filter gets an explicit `time_band_supported: false` instead). `council_summary` (`pipeline/reports/council.py: compute_council_summary`) pools the on-time/service-delivered rate into one whole-agency row, footnoted from `pipeline/reports/definition.py`'s `DefinitionMeta` via `format_definition_footnotes`. `delay_certificate` (same module's `compute_delay_certificate`) always live-scans ClickHouse for individual over-threshold departures — no `agg_*` fast path exists at that granularity. `ranking`/`ranking_best` leave out groups observed fewer than `RANKING_MIN_SAMPLES` times unless `include_sparse=1` (the screen's `sparse=1` toggle) and return `rows_total` and `reliable_min_samples`, so the screen can say how many rows qualified and badge the thin ones. `?format=csv` streams the same rows as a UTF-8-BOM CSV via `_csv_response`. |
| `useSuggestion(agencyId, exclude)` (drives `InsightPanel`) | `GET /api/{agency_id}/reports/suggest` (`api/routers/reports.py: get_suggestion`) | `pipeline/reports/suggest.py: compute_suggestion()` — a rule-based pick (anomaly over a 1-day window, or trend-shift/on-time over a 7-day window); polled every 5 minutes. |
| `useForecastOverview(agencyId)` / `useForecastHeatmap(agencyId, route)` (both drive `RouteForecastSection`) | `GET /api/{agency_id}/forecast/overview` / `GET /api/{agency_id}/forecast/heatmap?route=...` (`api/routers/reports.py`) | Both re-pool `agg_route_hour_dow` on read (a seasonal-naive baseline, explicitly **not** a prediction — both responses carry a `disclaimer` string). `forecast/overview`'s route list additionally joins the last 7 analyzed days from `agg_route_daily` for each route's sparkline (best-effort — a failure there degrades to no sparklines rather than a 500). |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/AnalysisTab.tsx` | Analysis tab shell: report-type selection, `TrendBlock`/`DowBandHeatmapCard`/`DwellRunBlock` composition |
| `frontend/src/components/ReportTable.tsx` | Generic report-row table renderer |
| `frontend/src/components/charts/DailyChart.tsx` | Trend report's daily line chart |
| `frontend/src/components/charts/HourlyHeatmap.tsx` | Trend report's hourly heatmap |
| `frontend/src/components/charts/DowBandGrid.tsx` | `BandGrid`/`Legend` — dow x time-band grid used by both the trend report and `RouteForecastSection` |
| `frontend/src/components/RouteForecastSection.tsx` | `route_forecast` report body (agency-wide + per-route views) |
| `frontend/src/components/InsightPanel.tsx` | Proactive single-suggestion panel |
| `frontend/src/components/InsightHint.tsx` | `?` hint popover explaining each report type |
| `frontend/src/routes/destinations.ts` | The per-screen report-type lists and `reportHref`, the report-type → screen map every report link goes through |
| `frontend/src/routes/legacyRedirects.tsx` | `analysis/<segment>`, `reports/:reportType` and `forecast` redirects to the screen hosting the report |
| `frontend/src/api/hooks.ts` | `useReports`, `useReport`, `useSuggestion`, `useForecastOverview`, `useForecastHeatmap` |

**Backend**

| File | Role |
|---|---|
| `api/routers/reports.py` | `/reports` (list), `/reports/{report_type}` (compute + CSV), `/reports/suggest`, `/forecast/heatmap`, `/forecast/overview` |
| `pipeline/reports/rankings.py` | The seven report-tab `compute_*` functions |
| `pipeline/reports/dwell_run.py` | `compute_dwell_run_decomposition` — the `dwell_run` report's read side |
| `pipeline/reports/council.py` | `compute_council_summary`/`compute_delay_certificate` — the `council_summary`/`delay_certificate` reports' read side |
| `pipeline/reports/definition.py` | `DefinitionMeta`/`resolve_definition_meta`/`format_definition_csv_line`/`format_definition_footnotes` — the shared on-time/late definition metadata every report/comparison view surfaces |
| `pipeline/dwell_run.py` | Pure dwell/running-time math (schedule + `arr_delay`/`dep_delay` → actual timestamps → dwell/running seconds) shared by the analyze-time builder and the read side |
| `pipeline/histogram.py` | Fixed-width histogram bucketing/percentile math, accepting custom `(lo, hi, width)` bounds so `pipeline/dwell_run.py` can reuse it with its own bucket scale |
| `pipeline/reports/suggest.py` | `compute_suggestion()` — the Insight Panel's rule engine |
| `pipeline/reports/forecast.py` | `summarize_agency_overview`, `summarize_expected_delay_heatmap`, `hourly_cells_to_dow_band` — shape the forecast endpoints' payloads |
| `pipeline/analyze.py` | Builds every `agg_*` table the fast paths and forecast endpoints read, including `agg_route_daily_dwell_run` (`static_join` agencies with a static schedule loaded only) |

## How to verify manually

**Automated tests:**

- Backend: `tests/api/test_reports.py`, `tests/api/test_forecast_heatmap.py`,
  `tests/api/test_forecast_overview_endpoint.py`,
  `tests/unit/test_forecast_heatmap.py`, `tests/unit/test_forecast_overview.py`,
  `tests/unit/test_reports_rounding.py`, `tests/unit/test_dwell_run.py`
  (pure dwell/running-time math), `tests/pipeline/test_analyze.py`'s
  `agg_route_daily_dwell_run` cases, `tests/unit/test_definition_meta.py`
  (definition-metadata resolution/rendering, both the CSV line and the
  locale-aware report-template footnotes), `tests/unit/test_council_report.py`
  (pure scheduled → actual clock-time arithmetic).
- Frontend: `frontend/src/components/ReportTable.test.tsx`,
  `frontend/src/components/charts/DowBandGrid.test.tsx`,
  `frontend/src/components/RouteForecastSection.test.tsx`,
  `frontend/src/components/InsightPanel.test.tsx`,
  `frontend/src/tabs/destinationTabs.test.tsx` and
  `frontend/src/tabs/RoutesIndex.test.tsx` (which screen hosts which
  report types, and `sort`), `frontend/src/routes/destinations.test.ts`
  (every report type has exactly one home).

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load data first:
   `make fetch-ingest` (or `ingest_live` + `make load_static`), then
   `make analyze` for the agency - otherwise every report shows the
   no-data empty state.
2. Click "Time" in the rail -> URL `/agencies/:agencyId/time`; expect its
   first report (`trend`) to be selected.
3. Click each report-type button in the left column, and repeat on the
   Routes, Why and Compare rail entries - expect the URL's `report` param to
   update and the body to show either a table (with a working CSV download
   link) or, for `trend`, the daily chart + hourly heatmap + dow-band grid.
   Open `/agencies/:agencyId/routes?sort=on_time` - expect `on_time` to be
   selected.
4. On Time, click "Route forecast" - expect the agency-wide grid/route list; select
   exactly one route in the filter bar - expect the view to switch to the
   per-route detail (band-collapsed grid, worst-window sentence).
5. Change the filter bar's date range / dow / time_band - expect the
   selected report to refetch with new numbers.
6. If the Insight Panel is visible (dev builds default it on; set
   `localStorage.transit.insightPanelEnabled = "1"` otherwise), click its
   suggestion - expect navigation to the relevant report/route; dismiss it
   and confirm it doesn't reappear this session.

## i18n

- Frontend strings live under the `reports.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus `forecast.*` (dow/band labels shared with
  `RouteForecastSection`). The host screens' rail labels are `nav.routes`,
  `nav.time`, `nav.why` and `nav.compare`.
- Server-side CSV column headers are hardcoded Japanese in
  `api/routers/reports.py`'s `_REPORT_CSV_COLUMNS` (operator-facing
  downloads, not routed through `_LOCALES` - update this table directly if a
  report's column set changes).
- Every JSON report response and CSV export carries a `definition` block
  (`pipeline/reports/definition.py`) stating the on-time/late tolerance,
  measurement point, dedup rule, and exclusion threshold that produced the
  numbers — resolved from the same tolerance params `get_report` validates,
  never hardcoded, so a custom-tolerance export can't silently read back as
  the `legacy_60s` default. In the CSV it's a one-cell preamble row before
  the column header; in the UI, `DefinitionMetaBlock` renders it from the
  API response's `definition` field.
