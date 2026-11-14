# Network tab

Cross-agency health board over a date range: the agencies whose data
supports a comparison ranked by average delay, and every other agency listed
after them by name with the reason it is not ranked. A ranking is an action
for the council that reads it, so the board ranks only what the data can
defend; see [Comparability gate](#comparability-gate).

## How a user reaches it

- Route: Compare by agencies, `/agencies/:agencyId/compare?by=agencies`,
  rendered by `frontend/src/tabs/CompareTab.tsx` (the rail's Compare entry,
  `nav.compare`). Its two-button switch (`compare.mode_periods` /
  `compare.mode_agencies`) sets `by`: `by=agencies` shows this board, and
  any other `by` (default `periods`) shows the `compare_ranking` report.
  `/agencies/:agencyId/network` and `analysis/compare?mode=agencies`
  redirect here, keeping the rest of the query string (`mode=agencies`
  becomes `by=agencies`), and so does a bare `/network` bookmark, via
  `frontend/src/routes/networkRedirect.tsx: RedirectNetworkToAgencyNetwork`
  (to the last-used agency, or to `/` when none is remembered).
- Top-level component: `frontend/src/tabs/NetworkTab.tsx`. Unlike every other
  tab, it does **not** show the scope sentence, and the board's endpoint reads
  only the scope's `from`/`to` date range (whole-agency comparison, per the
  endpoint's own docstring) — the shared dow/service/time_band/route filters
  do not change the figures. The other scope params stay in the URL.

What the user sees/does:

- **Eyebrow + title + help text**, and a collapsible "how to read this"
  `<details>` explaining each column (avg delay, on-time % and its interval,
  observation coverage, the ranking rule with the gate's thresholds, samples,
  feed health, freshness, coverage).
- **From/to date pickers** — HTML `<input type="date">` elements that read
  and write the scope's `from`/`to` URL params through `useScope`, as every
  scoped screen does.
- **Agency card list** — one card per agency. The comparable agencies
  (`comparable`) come first, sorted client-side worst-avg-delay first
  (`byDelayDescending`), agencies whose data stopped after the current ones
  and agencies with no data in range last (`dataRank`). Then, under a quiet
  `h3` divider ("Not ranked: the data does not support a comparison" /
  「順位を付けない事業者（データが比較に足りません）」) that states the
  gate's thresholds once, the other agencies in name order (`byAgencyName`,
  the agency picker's Japanese collation; the API returns them in
  `agency_id` order and never by delay), each with its
  `comparable_reason` in words beside the figure and threshold it missed,
  such as "Observation coverage 41.0% (below 70%)". The divider is left out
  when every agency is ranked. "Stopped" is the agency picker's rule
  (`hasStopped` in `components/agencyDirectory.ts`): the row's `data_to`
  trails the newest `data_to` on the board by three days or more, and the
  row then reads the picker's "No updates after <date>". Each card shows:
  agency name (links to that agency's Live view, carrying the current scope
  minus its `routes` and `stop` params, which name one agency's codes), its
  own data range only where it differs from the period (the period itself
  is the eyebrow, in the same date style), avg delay + on-time % with its 95%
  interval under it (left out where no trip count bounds it, and in the
  ridership-weighted view, which it does not describe), observation coverage
  ("Observation coverage 90.0% (900 of 1,000 planned trips)", or "—" where it
  cannot be measured), planned trips for the period (`planned_trips`, never the loaded timetable's
  `planned_trip_count`; when a timetable plans only some of the window's
  days, by a count saved for the day or the loaded timetable, it carries
  "N/M days a timetable plans" and a footnote under the list says every
  plan-based figure counts only those days), service-delivered % (executed
  trips ÷ planned trips — reads "—" rather than a misleading 100%, followed
  by the reason from `delivered_missing`: no plan this period, a feed format
  that cannot report cancellations, a feed no live verdict has confirmed, or
  a feed that reported no trip not run (CANCELED, or every stop it reports
  with a delay or a status SKIPPED; a stop reported with neither counts
  neither way) in the period or the 90 days up to its end
  (`feed_reports_no_cancellations`: nothing would ever
  come off the plan, so the rate would read 100% whatever ran; the evidence
  is a row in `agg_service_delivered_daily`, so the check reads Postgres,
  never `updates`, and the rate returns once the feed reports one);
  when the planned figures cannot be read at all, `delivered_missing` is
  `unreadable`, planned trips read "—" rather than 0, and the delay figures
  still show), vehicle-km delivered % only beside a delivered rate ("—" when
  no shapes are loaded), a delay bar on one axis shared by every card
  (`delayAxisMax` in `components/charts/delayAxis.ts`: 6, 12, 18 or 24
  minutes, the smallest step holding the latest-running agency; marked at 5
  minutes) filled with the shared delay steps (`delayRampVar()`), sample
  count, and two optional flags: an amber (`--error-fg`) dot when
  `clamp_pct` (implausible/clamped readings) is above 1%, and a "Behind" badge
  (`network.stale_badge`) when the feed hasn't reported recently. The agency
  selected in the URL (there is no per-user agency) is highlighted and
  tagged "Viewing".
- **Definition metadata block** (`DefinitionMetaBlock`, a collapsed
  `<details>` disclosure above the card list) — states the on-time/late
  tolerance, measurement point, dedup rule, and exclusion threshold behind
  every card's on-time %,
  from the response's `definition` field (`pipeline/reports/definition.py`).
  This board has no tolerance query param of its own, so it always counts by
  the default preset, `standard_m1_p5` (`BOARD_ON_TIME_WINDOW` in
  `pipeline/reports/comparability.py`: no more than 1 minute early and 5
  late) — surfaced explicitly so it can be checked against a per-agency
  report's own `definition` block instead of assumed.
  It ends with the method version, the newest day any listed agency's
  aggregates changed, and each listed agency's timetables (each with its
  content hash where the archive maps it, as `docs/features/analysis-tab.md`
  describes) and observation sources (archive, live or none recorded, in
  days) over the range, listed under the agency's name, then each listed
  agency's feed quality (observation coverage and implausible share), which
  `api/routers/network.py` states from the board's own rows
  (`pipeline/reports/network.py: board_feed_quality`).
- **Ridership-weighted on-time toggle** — a checkbox next to the from/to date
  pickers, present only when at least one agency in the current list has a
  manually-configured ridership weight (`pipeline/reports/ridership.py`,
  backed by the `ridership_weights` table — there is no IC-card/APC ridership
  source in this pipeline, so weights are entered by hand). When checked, an
  agency with a configured weight shows its ridership-weighted on-time %
  (each route's contribution pooled by its weight, so a low-ridership route's
  poor performance moves the headline rate less than counting every route
  equally would) instead of the plain on-time %, suffixed "(weighted)"; an
  agency with no configured weight keeps showing its plain figure regardless
  of the toggle. Absent entirely (not just unchecked) when no agency has any
  configured weight — a toggle that would only ever show a no-op view
  identical to the unweighted one is never rendered.

## Comparability gate

`pipeline/reports/comparability.py: assess()` decides, per agency and
period, whether the board may rank it (`comparable`) and otherwise the first
check it fails (`comparable_reason`), and returns the figures it judged. It is
the gate's one entry: it drops the trip counts of a strategy whose trip_ids
cannot be matched to the timetable before computing anything from them. The
thresholds are constants there, each inclusive, and the response serves them
as `comparability`, so the UI quotes the figures the server applied. The
checks run in a fixed order:

| Order | Reason | Fails when | Why |
|---|---|---|---|
| 1 | `no_data` | no observation in the period | nothing to compare |
| 2 | `coverage_unknown` | the ingest strategy's trip_ids cannot be matched to the timetable's (only `static_join` can; `aomori_regex` parses its own), or no timetable plans the days observed, or the plan could not be read | coverage cannot be measured, so nothing shows the figures rest on most of the service |
| 3 | `coverage_low` | observation coverage below `MIN_COVERAGE_PCT` (70%) | the feed's gaps could hide any delay pattern, and their bias differs feed by feed |
| 4 | `ci_wide` | the on-time interval's half-width above `MAX_CI_HALF_WIDTH_PP` (3 points) | the interval then spans more than the gap between many neighbouring agencies' rates, so ranking them would order noise |
| 5 | `clamp_high` | `clamp_pct`, to a tenth as printed, above `MAX_CLAMP_PCT` (5%) | a feed producing that many implausible readings makes the readings it kept suspect too |

Each row also carries its agency's `capability_tier`
(`pipeline/reports/capability.py`), derived from the same facts as the gates:
`basic` when the agency's trip_ids cannot be matched to the timetable, its
ingest strategy cannot carry the RT-optional fields, or a live RT field-coverage
probe found them missing; `full` otherwise, including a capable feed whose probe
lapsed or never ran. A basic row is labelled "Basic data: on-time only", and its
service-delivered rate and unmeasurable coverage say they are not part of basic
data instead of giving a pipeline reason. The agency catalogue
(`GET /api/agencies`) serves the same tier for the picker and every agency page.

Each check reads the figure the row prints, rounded as printed, so an
operator can work out from the row itself why it is unranked: the reason line
names the figure and the threshold, and the figures behind it (coverage with
its observed and planned trips, the interval, the implausible share) sit in
the same row. The board prints a share to a tenth, ties away from zero
(`roundTo` in `frontend/src/utils/format.ts`), so the gate rounds `clamp_pct`
the same way: 5.04% prints 5.0% and passes, 5.05% prints 5.1% and fails.

- **Observation coverage** (`coverage_pct`) is `observed_trips` ÷
  `coverage_planned_trips` × 100, to one decimal and not capped: over 100%
  means the feed reported trips the timetable doesn't name, and stays
  visible. `observed_trips` sums `agg_route_daily.trips_observed` over the
  days a timetable plans (the same `planned_days` the planned figure counts,
  in `pipeline/reports/service_delivered.py`), and `coverage_planned_trips`
  sums the plan over the same days. Both stop at `coverage_through`, served
  once on the summary beside `comparability` (`coverage_through()` in
  `pipeline/reports/network.py`): the newest day any agency on the board has
  observations for in the period. A later day is one the aggregates have not
  reached for anyone yet, as the period's last day is until its promotion and
  analyze run, and counting it would lower every feed's coverage alike. A gap inside that span counts
  against the feed that has it, and so do an agency's own trailing days when
  its figures fall behind the others'. A footnote under the list names the
  day when it is before the period's end. `planned_trips` itself still spans
  the whole period. `observed_trips` and `coverage_planned_trips` are `None`
  for a feed whose trip_ids cannot be matched to the timetable and for a plan
  that could not be read; with no plan through `coverage_through`,
  `coverage_planned_trips` is 0 and `observed_trips` `None`. `coverage_pct` is
  `None` in each of those cases.
- **On-time interval** (`ci_low`/`ci_high`) is the 95% Wilson interval of
  `on_time_pct` with the stop-event share as the proportion and
  `observed_trips` as the sample size, because the stops of one trip run late
  together and are not independent draws; counting stop events would claim an
  interval several times too narrow. The bounds round outward to one decimal.
  Without a trip count there is no interval (`None`), never one computed from
  the stop-event count. A planned-days trip count against a whole-period
  share errs wide for an agency only part of whose period a timetable plans.
- **A missing `clamp_pct`** (no `agg_feed_health` row in range) passes: analyze
  writes that table in the same run as the delay figures, so its absence says
  nothing about the feed.

The council report gives its one agency's verdict from this board's own
cached result for the same dates (its row of `compute_network_summary`), so
the two never disagree; see `docs/features/reports-tab.md`.

### What the board does and does not correct for

An operator contesting a figure should know what it is and is not adjusted
for:

- **Counted:** stop events reported in the period, the last value the feed
  sent for each (`latest_observation_per_stop_event`). Coverage shows how much
  of the plan those reports cover, and the interval how far the on-time share
  could move by chance.
- **Counted — early departures:** on time is the industry window, so a
  departure more than 1 minute early is not on time, and an agency that runs
  early does not read better than one that holds to time. The on-time count
  and the ridership-weighted one are read off each day's exact columns
  (`pipeline.histogram.on_time_count_sql`), with no histogram merged. The
  council report and the Routes table give the early-departure rate beside
  the on-time rate.
- **Not corrected — feed density:** how many stops per trip, and how often, a
  feed reports differs by vendor and by whether a day was ingested live or
  from an archive. The interval counts trips so that density does not narrow
  it, but the on-time share is still the share of whatever stop events were
  reported.
- **Not corrected — slack placement:** a timetable that puts its recovery
  time before the last stop shows lateness mid-route and earliness at the
  terminal; one that spreads it evenly can look on time with the same
  operation.
- **Not corrected — the value itself** is the last delay the operator's
  feed reported for each stop, which can be the vendor system's prediction
  rather than a recorded departure; the app does not observe vehicles
  independently, so a predictor's bias carries into the figure.

The gate does not group agencies by vendor platform or frequency, and the
default order is still by delay among the comparable agencies.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useNetworkSummary(ctx)` (only `ctx.from`/`ctx.to` are used) | `GET /api/network/summary?from=...&to=...` (`api/routers/network.py: network_summary`, not scoped under `/api/{agency_id}` like every other endpoint in this doc set) | `pipeline/reports/network.py: compute_network_summary()` — `samples` (deduped observation count) and the avg-delay/on-time figures come from Postgres `agg_route_daily_dist`; `raw_samples`/`clamp_count` (feed health) come from `agg_feed_health`. Freshness (`is_stale`) compares each agency's max `agg_route_daily_dist` date against a live ClickHouse `newest_filed_date_before` probe per agency, the newest service date its readings file a stop event under (one indexed read each) — a missing live timestamp is treated as "not stale" rather than failing, mirroring `today_route_summary`'s own freshness try/except in `api/routers/map.py`. `planned_trips`/`executed_trips`/`service_delivered_pct` come from `pipeline/reports/service_delivered.py: compute_service_delivered_by_agency()` — planned trips per day from Postgres `planned_trips_daily` where a day's count was saved, else from the loaded `static_calendar`/`static_calendar_dates`/`static_trips` (each service's weekly running days plus its per-date additions, minus its removals), non-executed trips summed from the precomputed Postgres `agg_service_delivered_daily` (built once per agency by `pipeline.analyze.analyze()` from a ClickHouse `updates` scan keyed on `schedule_relationship_trip`/`schedule_relationship_stop`); `covered_days`/`window_days` say how many of the range's days have a plan, saved or from the loaded timetable, the only days all three count; `executed_trips`/`service_delivered_pct` read `None` for an agency without a live, complete RT field-coverage verdict (`rt_field_coverage_confirmed_agencies()` in `pipeline/strategies/static_join.py`, which also requires `ingest_strategy` `static_join`), or that has no static schedule loaded, and `delivered_missing` says which (`no_plan`, `feed_cannot_report`, `feed_unverified`, or `unreadable` when the read itself failed). `observed_trips`/`coverage_planned_trips` come from the same `compute_service_delivered_by_agency()` read (`agg_route_daily` over its planned days, through the summary's `coverage_through`), and `coverage_pct`/`ci_low`/`ci_high`/`comparable`/`comparable_reason` from `pipeline/reports/comparability.py` (see [Comparability gate](#comparability-gate)); the gate's thresholds are the response's top-level `comparability`. `has_ridership_weights`/`weighted_on_time_pct` come from `pipeline/reports/ridership.py: compute_ridership_weighted_on_time_by_agency()` — reads the manually-populated Postgres `ridership_weights` table joined against `agg_route_daily_dist`'s per-route `on_time_count`/`samples`; both fields are `False`/`None` for an agency with no rows in `ridership_weights` at all. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/NetworkTab.tsx` | Network tab: date pickers, ranked card list rendering |
| `frontend/src/components/comparabilityReason.ts` | `unrankedReason()` — an unranked agency's reason in words, shared with the council report's comparison tile |
| `frontend/src/tabs/CompareTab.tsx` | Compare screen: the `by` switch between this board and `compare_ranking` |
| `frontend/src/routes/networkRedirect.tsx` | Bare `/network` → `/agencies/{lastAgencyId}/compare?by=agencies` redirect (or `/` with no remembered agency) |
| `frontend/src/styles/tokens.ts` | `delayColor()` — shared warm-ramp coloring used by the delay bar |
| `frontend/src/api/hooks.ts` | `useNetworkSummary` |

**Backend**

| File | Role |
|---|---|
| `api/routers/network.py` | `GET /api/network/summary` |
| `pipeline/reports/network.py` | `compute_network_summary()`, whose cached rows also give the council report its verdict |
| `pipeline/reports/comparability.py` | The comparability gate: thresholds, observation coverage, the trip-counted on-time interval, `assess()` |
| `pipeline/reports/service_delivered.py` | `compute_service_delivered_by_agency()` — planned-vs-executed trip counts |
| `pipeline/planned_trips.py` | The per-day planned-trip rule, and the capture that saves each day's count into `planned_trips_daily` |
| `pipeline/reports/ridership.py` | `compute_ridership_weighted_on_time_by_agency()` — the manually-populated `ridership_weights` table and the weighted on-time metric it powers |
| `api/clickhouse.py` | `newest_filed_date_before_by_agency` — the per-agency live-freshness probe |
| `pipeline/analyze.py` | Builds `agg_route_daily_dist`, `agg_feed_health`, and `agg_service_delivered_daily` — the aggregates this tab reads |
| `db/migrations/0035_ridership_weights.up.sql` | The `ridership_weights` table (agency-level default and/or per-route weight rows) |

## How to verify manually

**Automated tests:**

- Backend: `tests/api/test_network.py`, `tests/unit/test_comparability.py`,
  `tests/pipeline/test_service_delivered.py` (observed trips and the coverage
  bound).
- Frontend: `frontend/src/tabs/NetworkTab.test.tsx`,
  `frontend/src/routes/networkRedirect.test.tsx`,
  `frontend/src/tabs/destinationTabs.test.tsx` (Compare's `by` switch).

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load and analyze
   data for at least two agencies to see a meaningful ranking:
   `make fetch-ingest` (or `make load_static` before `ingest_live`) then
   `make analyze` per agency — a single agency still renders (one card),
   just without a comparison.
2. Click "Compare" in the rail, then its "Agencies" button → URL
   `/agencies/:agencyId/compare?by=agencies`.
3. Expect the comparable agencies first, worst-avg-delay first, with the
   current agency's card visually highlighted and tagged "Viewing"; then,
   below "Not ranked: the data does not support a comparison", the rest in
   name order, each with its reason. A short period (a few days) usually
   leaves the smaller agencies unranked as `ci_wide`.
4. Click another agency's name — expect navigation to that agency's
   Live view, carrying the same scope in the URL query string minus
   `routes` and `stop`.
5. Change the from/to date pickers — expect the URL's `from`/`to` to update
   and the whole list to refetch and re-rank (note: the figures do **not**
   honor the dow/service/time_band/route filters used elsewhere, by design).
6. If any agency has a clamp rate above 1% or a stale feed, expect the amber
   dot / "Behind" badge to render on that card; hover the badge for its
   tooltip text.
7. Visit the bare `/network` URL directly — expect an immediate redirect to
   `/agencies/{lastKnownAgencyId}/compare?by=agencies`.
8. With no rows in `ridership_weights` for any agency, expect no
   "Weight by ridership" checkbox at all. Insert a row (e.g.
   `INSERT INTO ridership_weights (agency_id, route_code, weight) VALUES
   (1, NULL, 5)`) for one agency, reload, and expect the checkbox to appear;
   checking it should swap that agency's on-time % for its ridership-weighted
   figure (suffixed "(weighted)"), leaving every other agency's figure
   unchanged.

## i18n

- Frontend strings live under the `network.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus the shared `common.date_range` string (through
  `formatDateRange()` in `frontend/src/utils/format.ts`) used in the coverage
  line.
