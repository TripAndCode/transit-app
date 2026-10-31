# Analysis tab

Dense, desktop-oriented report browser: a list of live-queried report types
(ranking, on-time rate, trend, dow comparisons, ...) plus a route-forecast
section, each scoped to the shared date/dow/time-band/service/route filter,
with a proactive "Insight Panel" suggesting what to look at next.

## How a user reaches it

- Hosted by three of the rail's destinations. Each passes AnalysisTab the
  report types it hosts (the per-screen lists in
  `frontend/src/routes/destinations.ts`):
  - Time (`/agencies/:agencyId/time`, `frontend/src/tabs/TimeTab.tsx`) —
    `trend`, `dow_weekday`, `dow_weekend`, `route_forecast`;
  - Why (`/agencies/:agencyId/why`, `frontend/src/tabs/WhyTab.tsx`) —
    `dwell_run`, with the rain (`WeatherDelayPanel`) and long-gap
    (`HeadwayQualityPanel`) panels beside it. `WhyAnalyses`
    (`frontend/src/components/analysis/WhyAnalyses.tsx`) draws the three:
    the ones with something to show first, in full, and each one that can't
    be shown once in a closing 「表示できない分析」 list, with a status chip and
    the sentence giving its cause (`components/analysis/notShown.ts`), not
    as an empty card;
  - Compare (`/agencies/:agencyId/compare`, `frontend/src/tabs/CompareTab.tsx`)
    for any `by` other than `agencies` (default `periods`) —
    `compare_ranking`.

  Reports hosts it too, for the export-style types `council_summary` and
  `delay_certificate` (`/agencies/:agencyId/reports?doc=council` or
  `?doc=certificate`, see `docs/features/reports-tab.md`).
- Routes (`/agencies/:agencyId/routes`, `frontend/src/tabs/RoutesIndex.tsx`)
  is not an AnalysisTab screen: `frontend/src/tabs/RouteTableScreen.tsx`
  lists every route in one table, the `route_table` report, sorted by the
  figure the reader picks. See "The Routes table" below.
- Redirects keep the query string: `/agencies/:agencyId/analysis/:reportType`
  and `/agencies/:agencyId/reports/:reportType` send a report-type segment to
  the screen hosting it (with `report=<type>` on Time, Why or Compare, the
  Routes table sorted as that list was ordered for `ranking`, `ranking_best`,
  `on_time` and `worst_5min`, and `doc=council` / `doc=certificate` on
  Reports). The named `analysis/`
  segments go to their screens: `when` to Time, `why` to Why, `rider` to
  `routes?sort=on_time`, `compare` to `compare?by=periods` (`by=agencies`
  when `mode=agencies`), `overview` and `predict` to Pulse, and `where` as
  described in `docs/features/route-analysis-tab.md`. Any other
  `analysis/` or `reports/` segment goes to Pulse.
  `/agencies/:agencyId/forecast` goes to `time?report=route_forecast`
  (`frontend/src/routes/legacyRedirects.tsx`, through `reportHref` in
  `destinations.ts`).
- Reach it from the Time, Why and Compare rail entries, the command
  palette's report items (through `reportHref`, which opens the screen
  hosting the type), or the Reports period summary's "Detailed reports →"
  link (`time?report=trend`).
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
  `frontend/src/components/ReportTable.tsx` renders the rows, and the report
  heading carries a CSV download link. An empty result shows `EmptyState`
  naming the active filters, with recovery actions from
  `frontend/src/components/emptyStateRecoveries.ts`, each shown only when it
  applies: clear the route filter, reset the service type to all, and jump
  to the latest data. Admins also get a collapsed raw-rows `<details>` under
  any report other than `trend` or `route_forecast` that has rows; it shows
  the response's text summary. `trend` instead renders `TrendBlock` (defined inline in
  `AnalysisTab.tsx`): a day-of-week x hour heat surface with "By hour" and
  "By band" profiles
  (`frontend/src/components/charts/HeatSurface.tsx`; its bands, like the
  forecast grid's columns, are the `time_band` filter's own seven:
  `pipeline/reports/forecast.py`'s `BANDS` is built from `api/range.py`'s
  `TIME_BAND_RANGES`, and `frontend/src/api/timeBands.ts` mirrors the hours),
  a daily line chart
  (`frontend/src/components/charts/DailyChart.tsx`), and an hourly heatmap
  (`frontend/src/components/charts/HourlyHeatmap.tsx`). `dwell_run`
  instead renders `WhyAnalyses`, whose `DwellRunBlock` is a per-route
  dwell-time/running-time table decomposing `arr_delay` (populated only for
  `ingest_strategy == 'static_join'` feeds) and `dep_delay` separately from
  the existing delay histogram. The feed reports a stop's arrival and its
  departure in separate polls, and a trip's last stop only as an arrival, so
  analyze pairs them per stop visit (trip, service day, stop) with
  `pipeline/db.py`'s `build_stop_visits_ch_sql`, each side the latest
  plausible reading of its own kind under the same delay clamp. Dwell needs
  both sides of a visit; running time needs this visit's arrival and the
  previous visit's departure, which is how the terminus gets one. A route
  with neither a dwell nor a running
  sample in range is left out; when routes are in scope but every one is,
  the payload stays `available` and names `no_paired_samples`, which the
  not-shown list states without suggesting another filter (an empty scope
  carries no reason and gets the filters' own empty state). When the
  agency's arrival data isn't trusted
  (`available: false`), the split joins the not-shown list under the reason
  the payload names (`unavailable_reason`, from
  `pipeline.strategies.static_join.coverage_gap`): `feed_unsupported` (the
  ingest strategy cannot carry the fields), `probe_refuted` (the live
  verdict found one missing), `probe_expired` (the verdict lapsed or is
  partial and awaits the daily re-probe) or `never_probed`, with the JST day
  of the last probe in `last_checked`. A time-band filter this decomposition
  can't serve (`time_band_supported: false`) gets its own `EmptyState`;
  neither state is ever drawn as a misleading zero. `/headway_quality`
  carries the same two fields, and names `no_high_frequency_routes` ahead of
  any coverage reason when the timetable classified routes but none as
  high-frequency.
  `route_forecast` instead renders `frontend/src/components/RouteForecastSection.tsx`
  (agency-wide landing view, or a per-route detail view when exactly one
  route is selected in the shared filter - see its own file-header comment).
  `compare_ranking` instead renders
  `frontend/src/components/charts/CompareBars.tsx`: one row per route under
  the headings 路線 / 平日（分） / 土日祝（分） / 差, with both day types'
  figures and a sentence naming the later side and by how much (「平日のほうが
  3.9分遅い」, in a neutral ink, so the gap never reads as red against green
  and never flips meaning). A URL-backed `rank` (`gap`, the default, or
  `weekday`/`weekend`, the 平日 and 土日祝 day types) orders the list by the
  printed gap or by one side's level; the ranked side (平日 for the gap) is the
  solid bar, filled with the shared delay steps, and the other a dashed ghost.
  Every bar is drawn against one axis, `delayAxisMax` over both sides of
  every row (6, 12, 18 or 24 minutes), so switching the order never rescales
  it. On a phone the bar and the gap sentence take their own lines. The tab
  asks for the API's maximum length, so the whole compared list is ranked.
- **Insight Panel** (right column) —
  `frontend/src/components/InsightPanel.tsx`: a single proactive suggestion
  ("this route's trend just shifted", "on-time rate dropped", etc.) whose
  button opens that route's dossier. The card names the route with
  `RouteLabel`, so routes that share a number read apart, above the server's
  `reason_text`, which names the day or days the rule judged (the agency's
  last analyzed day, which lags the clock) and not the route. A suggestion
  once opened is not offered
  again this session (persisted to `sessionStorage`, keyed by agency). The
  panel collapses to a narrow rail, and is switched off entirely only through
  localStorage `transit.insightPanelEnabled`; defaults on in dev builds
  (`import.meta.env.DEV`), opt-in in production.
- Below ~640px (`MOBILE_BREAKPOINT_PX`,
  `frontend/src/hooks/useMediaQuery.ts`) the three columns stack vertically
  instead of side-by-side — this tab otherwise stays desktop-oriented by
  design (see the comment in `frontend/src/tabs/analysisTab.css`).

## The Routes table

`RouteTableScreen` asks `GET /api/{agency_id}/reports/route_table` for one row
per route over the scope:

- **Columns.** Average delay, on-time rate, the share and the count of
  departures over five minutes late, and observations. The on-time cell
  carries the 「誤差大」 badge when its 95% interval is wider than ±5
  points.
- **Sort.** `sort` picks the figure: `avg`, `on_time`, `late5_share` or
  `late5_count`. The routes that need attention come first; `order=best`
  turns the order around. The sorted column alone carries the bar and the
  weight, its header carries `aria-sort`, and on a phone it heads each card.
  The rail's Routes stops are the four sorts.
- **Former list links.** A `report` (or a `sort`) naming one of the route
  lists the table replaced opens it sorted the way that list was:
  - `ranking` → `avg`;
  - `ranking_best` → `avg` with `order=best`;
  - `on_time` → `on_time`;
  - `worst_5min` → `late5_count`.

  `routeTableView` in `frontend/src/routes/destinations.ts` resolves this;
  `report` wins over `sort`. `order` turns the view around only beside a
  table sort or the on-time list, the one former list with an order to
  choose; beside the others it is a leftover from that list and is
  ignored.
  Changing the sort, the order or a toggle writes `sort`/`order` and drops
  `report`. `reportHref` sends those four report types (the command palette's
  report items) to the table the same way.
- **Labels follow the rows.** The response names its own `sort` and
  `order`. While another sort loads, the table keeps showing the previous
  rows, and its sort marks and bar stay on the column those rows are sorted
  by.
- **One population for every sort.** Routes observed fewer than
  `RANKING_MIN_SAMPLES` times are left out unless `sparse=1`
  (`include_sparse`), whatever the sort. Retired route codes and
  zero-delay routes are left out unless `retired=1`/`zero_delay=1`.
- **The counts.** `retired_rows`/`zero_delay_rows` are counted over every
  route the table ranks, not the shown prefix, so they stay the same across
  sorts and lengths. The line above the table says how many each reason
  leaves out. `rows_total` is the count the table ranks under the current
  toggles. The table shows 50 rows, and 「すべて表示」 asks for the API's
  maximum.
- **Context.** The heading names the window (「30日間（…）の集計」), so its
  figures are not mistaken for the Pulse screen's shorter window. Under the
  table sit the on-time definition and what 「5分超」 counts.
  `HeadwayQualityPanel` and `PerformanceStandardPanel` follow the on-time
  sort.
- **Colour.** A delay bar's colour and length come from the figure the cell
  prints, rounded to a tenth, so two rows printing the same figure share a
  colour. The severe-delay dot other delay tables draw is left off.

## Scope

Every analysis screen reads and writes one shared scope in the URL through
`frontend/src/api/scope.ts` (`useScope`, `parseScope`, `scopeToQueryString`,
`applyScopePatch`). The backend validates the same parameters in
`api/range.py`'s `clamp_range_ctx`, except `late`/`early`, which each
endpoint that takes them declares as its own bounded query params.

| Param | Values |
|---|---|
| `from`, `to` | ISO dates |
| `dow` | `all`, `weekday`, `weekend`, or a comma list of `mon`…`sun` |
| `time_band` | the seven bands, or `all` |
| `hour` | `0`–`23`, or an inclusive range `a-b` |
| `service` | `all`, `平日`, `土曜`, `日祝`, `土日祝` |
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
- **`service` selects dates, not rows.** A value is the timetable a date runs
  by the agency's own calendar (`pipeline/day_types.py`), never the
  observations' raw `service_type`, which is a feed's own `service_id` and
  often empty. `api/range.py`'s `resolve_service_dates` turns it into the
  window's dates once per request, and every query filters on those dates.
  `土日祝` selects every weekend and holiday date. On an agency split
  平日 / 土日祝, `土曜` and `日祝` each select its 土日祝 dates, so a link shared
  across agencies never comes back empty; the scope control offers each
  agency's own buckets and shows such a value as the bucket it resolves to.
- **Invalid values.** An invalid value in the page URL falls back to that
  condition's default instead of reaching the API. The API itself answers
  422 for an invalid value of any scope field.
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
- **`late`/`early`.** They are the on-time tolerance, applied by `on_time`,
  `route_table`'s on-time column and `council_summary`. Elsewhere they are
  ignored and reported as `false`, including `worst_5min`, whose over-5-min
  threshold stays its own `late_tolerance_sec`. `route_table`'s over-5-min
  share and count keep that fixed threshold too. The scope summary reports `early` as `true`, since
  it bounds that endpoint's tolerance curve.
- **Split figures.** `council_summary` also answers `delivered_scope_applied`
  (null on every other report): its planned/executed/service-delivered figures
  and data-quality caveat come from per-agency aggregates and honour only
  `from`/`to`. Under a narrower filter its headline, footnotes, CSV preamble
  and planned-trips tile label those figures as the whole agency's.
- **Not yet honoured.** `hour`, `stop` and `dir` are accepted and validated,
  but no endpoint honours them yet: `hour` waits on `agg_route_hour_daily`,
  and none of today's aggregates carries `direction_id`.

### The scope sentence

Pulse, Routes, Time, Why, Compare by periods and the Reports summary state
the scope as one sentence above their content
(`frontend/src/components/scope/ScopeSentence.tsx`), for example
「青森市バスの全路線を、9/1〜9/28のすべての曜日・全時間帯で、定時は1分以内として見る」.

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
  - Time: the seven bands and all day.
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
  filters nothing and is never greyed; the period always filters, so it is
  greyed wherever `from` is `false`. A screen that passes nothing greys
  nothing. AnalysisTab uses the forecast overview's map for
  `route_forecast` (the route heatmap's when exactly one route is
  selected), and a report's map only once that report's response is
  the one on screen.
- **Pinned strip.** 「すべての条件を表示」 shows every control inline under the
  sentence; the choice is kept in localStorage `transit.scopePinned`.
- **Presets and reset.** Signed-in users save and load presets beside the
  sentence (`PresetMenu`), or on the page heading's row when the page has a
  `ScopePageHeader`. 「条件をリセット」 appears once any condition, the
  period included, differs from the default, and resets the dates too.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useScopeSummary(agencyId, scope, enabled)` | `GET /api/{agency_id}/scope/summary` (`api/routers/scope_summary.py: scope_summary`) | `pipeline/reports/scope_summary.py: compute_scope_summary()` — four reads of `agg_route_daily_dist` (span, daily, per-weekday, per-route) plus one merged histogram for the tolerance curve; no ClickHouse. |
| `useReports(agencyId)` | `GET /api/{agency_id}/reports` (`api/routers/reports.py: list_reports`) | Static metadata only — the fixed `_REPORT_TYPES` tuple, no DB read. |
| `useReport(agencyId, reportType, ctx, options)` | `GET /api/{agency_id}/reports/{report_type}` (`api/routers/reports.py: get_report`) | Computed live per request from `pipeline/reports/rankings.py`'s `compute_ranking` / `compute_dow_ranking` / `compute_on_time` / `compute_worst_5min` / `compute_trend_series` / `compute_compare_ranking` / `compute_hourly_heatmap` — each follows the repo-wide pattern of a precomputed-`agg_*` fast path with a live ClickHouse fallback for a `time_band`-narrowed request (`compute_hourly_heatmap` also under a `routes` filter, since `agg_hour_daily` pools every route) (see `AGENTS.md` and the `ask-tab.md` doc's "ranking family" note — these are the same functions the Ask tab's `top_n`/`on_time`/`trend`/`cmp_service` tools call). `route_table` reads `pipeline/reports/route_table.py`'s `compute_route_table` the same way: the exact scalar columns of `agg_route_daily_dist` by default, its merged histogram for a requested on-time tolerance, and a live ClickHouse scan under a `time_band`. It takes `sort`/`order` (a 400 on any other report but `on_time`'s `order`) and `include_sparse`, and returns `rows_total` and `reliable_min_samples`. `dwell_run` instead reads `pipeline/reports/dwell_run.py`'s `compute_dwell_run_decomposition` from `agg_route_daily_dwell_run` — no live fallback (a time-band filter gets an explicit `time_band_supported: false` instead). `council_summary` (`pipeline/reports/council.py: compute_council_summary`) pools the on-time rate over the scope's filter into one row beside the whole agency's service-delivered rate for the dates, footnoted from `pipeline/reports/definition.py`'s `DefinitionMeta` via `format_definition_footnotes`. `delay_certificate` (same module's `compute_delay_certificate`) always live-scans ClickHouse for individual over-threshold departures — no `agg_*` fast path exists at that granularity. Every per-route report is one row per route over all of its observations, never split by the observations' raw `service_type` (a feed's own `service_id`). In the tuple rows of `ranking`/`ranking_best`/`on_time`/`worst_5min`, the second element names the active service filter, null without one; `dow_weekday`/`dow_weekend` replace the `dow` and `service` filters with their own day type (the agency's 平日 dates, or every other date as 土日祝, from `api.range.resolve_service_dates`, so a public holiday on a weekday is a 土日祝 day), so theirs is always null; `compare_ranking` splits days the same way, so each of its two sides matches the corresponding dow report, and it and `hourly_heatmap` carry no service slot. `ranking`/`ranking_best` leave out routes observed fewer than `RANKING_MIN_SAMPLES` times unless `include_sparse=1` and return `rows_total` and `reliable_min_samples`, so the screen can say how many rows qualified and badge the thin ones. The route lists (`route_table`, `ranking`, `ranking_best`, `on_time`, `worst_5min`, `compare_ranking`, `dow_weekday`, `dow_weekend`) leave out retired route codes (`pipeline/route_catalog.py: retired_route_codes`) unless `include_retired=1` (the screen's `retired=1`), and routes whose every observation reads exactly 0 seconds of delay, a feed echoing the timetable rather than reporting delay (`zero_delay_route_codes`, judged over at least `ZERO_DELAY_DAYS` through the agency's last day in the window and at least `ZERO_DELAY_MIN_SAMPLES` observations), unless `include_zero_delay=1` (the screen's `zero_delay=1`). A code that is both counts as retired. Every combination lists the same other routes, and `retired_rows`/`zero_delay_rows` say how many of each rank among them (`pipeline/route_catalog.py: route_list`); `route_table` counts them over every route it ranks (`left_out_counts`). Kept ones come on top of `limit`. `on_time` rates every route and returns `rows_total` counted the same way, so a caller can reach every route; it lists the lowest on-time rate first unless `order=best` (a 400 on any report but this and `route_table`), and each row's 7th element, `margin_pp`, is the rate's 95% Wilson half-width in points, rounded up to 0.1, which the 「誤差大」 badge and the CSV's `誤差(±ポイント)` column show. `compare_ranking` returns `rows_total` too, so the compare bars can say when the API's longest list still stops short of every compared route. `delay_certificate` returns `rows_total` as well, counted in the same scan, and its summary text and CSV footnotes say when `limit` left only the earliest trips listed. `?format=csv` streams the same rows as a UTF-8-BOM CSV via `_csv_response`. |
| `useSuggestion(agencyId, exclude)` (drives `InsightPanel`) | `GET /api/{agency_id}/reports/suggest` (`api/routers/reports.py: get_suggestion`) | `pipeline/reports/suggest.py: compute_suggestion()` — a rule-based pick (anomaly over a 1-day window, or trend-shift/on-time over a 7-day window); polled every 5 minutes. |
| `useForecastOverview(agencyId)` / `useForecastHeatmap(agencyId, route)` (both drive `RouteForecastSection`) | `GET /api/{agency_id}/forecast/overview` / `GET /api/{agency_id}/forecast/heatmap?route=...` (`api/routers/reports.py`) | Both re-pool `agg_route_hour_dow` on read (a seasonal-naive baseline, explicitly **not** a prediction — both responses carry a `disclaimer` string). With the `ml_forecast` flag on (off by default), fresh `agg_route_forecast_ml` rows replace the matching cells, marked `source: "ml"`, and the disclaimer then says some cells are a forecast model's estimate. An ML value is the model's point forecast, not an average, so `forecast/overview`'s `worst` carries its band's `source` too, and the section words an ML-sourced worst window as an expected delay and names the route figure in its detail "expected" rather than an average. A heatmap ML cell also carries its `q10`/`q90` (null on a past-average cell), the 10th-90th percentile range the section words as 「10回中8回は…の範囲」 on that cell and on the route headline's latest hour, never on a band, since bounds averaged over hours or routes are not an 80% range; a range whose point falls outside it is not shown. Bands pool every hour, and on the agency grid every route-hour, by its past observations, with the forecast's figure where there is one and the past average otherwise (`summarize_agency_overview` server-side, `collapseToBands` for a route); `_fetch_ml_cells` joins each forecast row's own route-hour history for that weight. The route headline names the latest hour first, then the latest band with its hours. Where any cell is the model's, a cell or band showing a past average instead is ringed and named so in its label and the legend. `forecast/overview`'s route list additionally joins the last 7 analyzed days from `agg_route_daily` for each route's sparkline (best-effort — a failure there degrades to no sparklines rather than a 500). Both responses name their dates: `forecast_days` lists the service date each ML weekday row forecasts, and `history_from`/`history_to` bound the analyzed days the past averages pool. `agg_route_hour_dow` keeps no date, so the overview reads them from `agg_hour_daily`, which keeps the same rows (those with a scheduled time), and the heatmap from its route's `agg_route_daily` days, an outer bound. The overview's `recent_from`/`recent_to` are the sparklines' window over `agg_route_daily`, read by the sparkline query itself, so they are null whenever the sparklines are. Like the sparklines, a failed span read degrades to null dates rather than a 500, and the heatmap skips its route span for a route with no pooled rows. The section states those dates above the grid, dates each forecast row, and sets the route list apart under its own past-measurements heading. Its grids, bars and route bars shade by the shared delay steps (`delayRampVar`, labelled by `DelayScaleLegend`), which are anchored on minutes, so a delay keeps its colour whatever else the view holds and matches the Time page's other grids. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/AnalysisTab.tsx` | Analysis tab shell: report-type selection, `TrendBlock`/`DwellRunBlock` composition |
| `frontend/src/tabs/RouteTableScreen.tsx` | The Routes screen's one sortable table (`route_table`) |
| `frontend/src/components/ReportTable.tsx` | Generic report-row table renderer |
| `frontend/src/components/charts/DailyChart.tsx` | Trend report's daily line chart |
| `frontend/src/components/charts/HourlyHeatmap.tsx` | Trend report's hourly heatmap |
| `frontend/src/components/charts/DowBandGrid.tsx` | `BandGrid`/`Legend` — dow x time-band grid used by `RouteForecastSection` |
| `frontend/src/components/charts/HeatSurface.tsx`, `heatSurfaceModel.ts` | The trend report's dow x hour surface: the two profiles, the faint low-confidence cells, and the generated row/column dim rules |
| `frontend/src/components/charts/CompareBars.tsx`, `compareBarMath.ts` | `compare_ranking` report body: gap or per-side ranking, ghost bar, and the printed gap as a sentence |
| `frontend/src/components/charts/delayAxis.ts` | The stepped delay axis (6/12/18/24 min) the compare bars and the Network screen's agency bars each pick from their own figures |
| `frontend/src/components/RouteForecastSection.tsx` | `route_forecast` report body (agency-wide + per-route views) |
| `frontend/src/components/InsightPanel.tsx` | Proactive single-suggestion panel |
| `frontend/src/components/InsightHint.tsx` | `?` hint popover explaining each report type |
| `frontend/src/routes/destinations.ts` | The per-screen report-type lists, `reportHref` (the report-type → screen map every report link goes through), and the Routes table's sorts and `routeTableView` |
| `frontend/src/routes/legacyRedirects.tsx` | `analysis/<segment>`, `reports/:reportType` and `forecast` redirects to the screen hosting the report |
| `frontend/src/api/hooks.ts` | `useReports`, `useReport`, `useSuggestion`, `useForecastOverview`, `useForecastHeatmap` |

**Backend**

| File | Role |
|---|---|
| `api/routers/reports.py` | `/reports` (list), `/reports/{report_type}` (compute + CSV), `/reports/suggest`, `/forecast/heatmap`, `/forecast/overview` |
| `pipeline/reports/rankings.py` | The seven report-tab `compute_*` functions |
| `pipeline/reports/route_table.py` | `compute_route_table`/`sort_route_table` — the Routes table, figure for figure as the per-figure reports compute it |
| `pipeline/reports/dwell_run.py` | `compute_dwell_run_decomposition` — the `dwell_run` report's read side |
| `pipeline/reports/council.py` | `compute_council_summary`/`compute_delay_certificate` — the `council_summary`/`delay_certificate` reports' read side |
| `pipeline/reports/definition.py` | `DefinitionMeta`/`resolve_definition_meta`/`format_definition_csv_line`/`format_definition_footnotes` — the shared on-time/late definition metadata every report/comparison view surfaces |
| `pipeline/dwell_run.py` | Pure dwell/running-time math (schedule + `arr_delay`/`dep_delay` → actual timestamps → dwell/running seconds) shared by the analyze-time builder and the read side |
| `pipeline/histogram.py` | Fixed-width histogram bucketing/percentile math, accepting custom `(lo, hi, width)` bounds so `pipeline/dwell_run.py` can reuse it with its own bucket scale |
| `pipeline/reports/suggest.py` | `compute_suggestion()` — the Insight Panel's rule engine |
| `pipeline/reports/forecast.py` | `summarize_agency_overview`, `summarize_expected_delay_heatmap`, `hourly_cells_to_dow_band` — shape the forecast endpoints' payloads |
| `pipeline/analyze.py` | Builds every `agg_*` table the fast paths and forecast endpoints read except `agg_route_forecast_ml` (loaded by `pipeline/ml_forecast_ingest.py`), including `agg_route_daily_dwell_run` (`static_join` agencies with a static schedule loaded only) |

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
  `frontend/src/components/charts/HeatSurface.test.tsx`,
  `frontend/src/components/charts/heatSurfaceModel.test.ts`,
  `frontend/src/components/charts/CompareBars.test.tsx`,
  `frontend/src/components/charts/compareBarMath.test.ts`,
  `frontend/src/components/RouteForecastSection.test.tsx`,
  `frontend/src/components/InsightPanel.test.tsx`,
  `frontend/src/tabs/destinationTabs.test.tsx` and
  `frontend/src/tabs/RoutesIndex.test.tsx` (which screen hosts which
  report types, and `sort`), `frontend/src/routes/destinations.test.ts`
  (every report type has exactly one home).

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load data first:
   `make fetch-ingest` (or `make load_static` before `ingest_live`), then
   `make analyze` for the agency - otherwise every report shows the
   no-data empty state.
2. Click "Time" in the rail -> URL `/agencies/:agencyId/time`; expect its
   first report (`trend`) to be selected.
3. Click each report-type button in the left column, and repeat on the
   Why and Compare rail entries - expect the URL's `report` param to
   update and the body to show either a table (with a working CSV download
   link) or, for `trend`, the daily chart + hourly heatmap + day x hour surface.
   Open `/agencies/:agencyId/routes?report=on_time` - expect the Routes table
   sorted by on-time rate, lowest first; pick another sort and expect `sort`
   to replace `report` in the URL.
4. On Time, click "Route forecast" - expect the agency-wide grid/route list; select
   exactly one route in the scope sentence - expect the view to switch to the
   per-route detail (band-collapsed grid, worst-window sentence).
5. Change the scope sentence's period / days / time - expect the
   selected report to refetch with new numbers.
6. If the Insight Panel is visible (dev builds default it on; set
   `localStorage.transit.insightPanelEnabled = "1"` otherwise), click its
   suggestion - expect navigation to that route's dossier; go back and
   confirm the same suggestion doesn't reappear this session.

## i18n

- Frontend strings live under the `reports.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus `forecast.*` (dow labels shared with
  `RouteForecastSection`) and `time_bands.*` (each band's name and hours). The host screens' rail labels are `nav.routes`,
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
