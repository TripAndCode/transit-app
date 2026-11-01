# Network tab

Cross-agency health board: every agency ranked by average delay over a
date range, so an operator (or a curious rider) can see how one agency
compares to the whole network at a glance.

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
  `<details>` explaining each column (avg delay, on-time %, samples, feed
  health, freshness, coverage).
- **From/to date pickers** — HTML `<input type="date">` elements that read
  and write the scope's `from`/`to` URL params through `useScope`, as every
  scoped screen does.
- **Ranked agency card list** — one card per agency, sorted client-side
  worst-avg-delay first (`byDelayDescending`), agencies whose data stopped
  after the current ones and agencies with no data in range last
  (`dataRank`). "Stopped" is the agency picker's rule
  (`hasStopped` in `components/agencyDirectory.ts`): the row's `data_to`
  trails the newest `data_to` on the board by three days or more, and the
  row then reads the picker's "No updates after <date>". Each card shows:
  agency name (links to that agency's Live view, carrying the current scope
  minus its `routes` and `stop` params, which name one agency's codes), its
  own data range only where it differs from the period (the period itself
  is the eyebrow, in the same date style), avg delay + on-time %, planned
  trips for the period (`planned_trips`, never the loaded timetable's
  `planned_trip_count`; when a timetable plans only some of the window's
  days, by a count saved for the day or the loaded timetable, it carries
  "N/M days a timetable plans" and a footnote under the list says every
  plan-based figure counts only those days), service-delivered % (executed
  trips ÷ planned trips — reads "—" rather than a misleading 100%, followed
  by the reason from `delivered_missing`: no plan this period, a feed format
  that cannot report cancellations, a feed no live verdict has confirmed, or
  a feed that sent no CANCELED trip or SKIPPED stop in the period or the 90
  days up to its end (`feed_reports_no_cancellations`: nothing would ever
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
  This board has no tolerance query param of its own, so it always resolves
  to the `legacy_60s` preset — surfaced explicitly so it can be checked
  against a per-agency report's own `definition` block instead of assumed.
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

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useNetworkSummary(ctx)` (only `ctx.from`/`ctx.to` are used) | `GET /api/network/summary?from=...&to=...` (`api/routers/network.py: network_summary`, not scoped under `/api/{agency_id}` like every other endpoint in this doc set) | `pipeline/reports/network.py: compute_network_summary()` — `samples` (deduped observation count) and the avg-delay/on-time figures come from Postgres `agg_route_daily_dist`; `raw_samples`/`clamp_count` (feed health) come from `agg_feed_health`. Freshness (`is_stale`) compares each agency's max `agg_route_daily_dist` date against a live ClickHouse `max_captured_at_before` probe per agency (one indexed read each) — a missing live timestamp is treated as "not stale" rather than failing, mirroring `today_route_summary`'s own freshness try/except in `api/routers/map.py`. `planned_trips`/`executed_trips`/`service_delivered_pct` come from `pipeline/reports/service_delivered.py: compute_service_delivered_by_agency()` — planned trips per day from Postgres `planned_trips_daily` where a day's count was saved, else from the loaded `static_calendar`/`static_calendar_dates`/`static_trips` (each service's weekly running days plus its per-date additions, minus its removals), non-executed trips summed from the precomputed Postgres `agg_service_delivered_daily` (built once per agency by `pipeline.analyze.analyze()` from a ClickHouse `updates` scan keyed on `schedule_relationship_trip`/`schedule_relationship_stop`); `covered_days`/`window_days` say how many of the range's days have a plan, saved or from the loaded timetable, the only days all three count; `executed_trips`/`service_delivered_pct` read `None` for an agency without a live, complete RT field-coverage verdict (`rt_field_coverage_confirmed_agencies()` in `pipeline/strategies/static_join.py`, which also requires `ingest_strategy` `static_join`), or that has no static schedule loaded, and `delivered_missing` says which (`no_plan`, `feed_cannot_report`, `feed_unverified`, or `unreadable` when the read itself failed). `has_ridership_weights`/`weighted_on_time_pct` come from `pipeline/reports/ridership.py: compute_ridership_weighted_on_time_by_agency()` — reads the manually-populated Postgres `ridership_weights` table joined against `agg_route_daily_dist`'s per-route `on_time_count`/`samples`; both fields are `False`/`None` for an agency with no rows in `ridership_weights` at all. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/NetworkTab.tsx` | Network tab: date pickers, ranked card list rendering |
| `frontend/src/tabs/CompareTab.tsx` | Compare screen: the `by` switch between this board and `compare_ranking` |
| `frontend/src/routes/networkRedirect.tsx` | Bare `/network` → `/agencies/{lastAgencyId}/compare?by=agencies` redirect (or `/` with no remembered agency) |
| `frontend/src/styles/tokens.ts` | `delayColor()` — shared warm-ramp coloring used by the delay bar |
| `frontend/src/api/hooks.ts` | `useNetworkSummary` |

**Backend**

| File | Role |
|---|---|
| `api/routers/network.py` | `GET /api/network/summary` |
| `pipeline/reports/network.py` | `compute_network_summary()` |
| `pipeline/reports/service_delivered.py` | `compute_service_delivered_by_agency()` — planned-vs-executed trip counts |
| `pipeline/planned_trips.py` | The per-day planned-trip rule, and the capture that saves each day's count into `planned_trips_daily` |
| `pipeline/reports/ridership.py` | `compute_ridership_weighted_on_time_by_agency()` — the manually-populated `ridership_weights` table and the weighted on-time metric it powers |
| `api/clickhouse.py` | `max_captured_at_before_by_agency` — the per-agency live-freshness probe |
| `pipeline/analyze.py` | Builds `agg_route_daily_dist`, `agg_feed_health`, and `agg_service_delivered_daily` — the aggregates this tab reads |
| `db/migrations/0035_ridership_weights.up.sql` | The `ridership_weights` table (agency-level default and/or per-route weight rows) |

## How to verify manually

**Automated tests:**

- Backend: `tests/api/test_network.py`.
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
3. Expect a ranked card list, worst-avg-delay first, with the current
   agency's card visually highlighted and tagged "Viewing".
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
