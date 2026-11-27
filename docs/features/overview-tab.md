# Overview tab

Magazine-style "how's the agency doing" landing page: one round-trip returns
a headline delta, a concentration/movers module, the heaviest hour and a
day-type comparison. Each of the questions another screen owns (which routes,
which hours, weekday against weekend, the trend) gets a short form here and a
link to that screen with the scope pre-set.

This is the period-summary view, not the realtime one: it is Pulse, the
rail's first destination, while the rail's Live entry opens the realtime map
(see `docs/features/map-tab.md`).

## How a user reaches it

- Route: Pulse, `/agencies/:agencyId/pulse`, which renders `OverviewTab`
  directly (`React.lazy`-loaded through `frontend/src/routes/lazyTabs.ts`).
  It is the default landing: a bare `agencies/:agencyId`
  (`frontend/src/main.tsx`'s `<Navigate to="pulse" replace />`) and a
  fresh/remembered agency selection
  (`frontend/src/components/OnboardingGate.tsx`) both land here. The
  `/agencies/:agencyId/analysis`, `analysis/overview`, `analysis/predict` and
  `period-overview` URLs redirect here, keeping their query string
  (`frontend/src/routes/legacyRedirects.tsx`).
- Rail nav link: the Pulse entry, first in
  `frontend/src/components/sidebarNavItems.ts`'s `SIDEBAR_NAV_ITEMS`
  (`nav.pulse` — "Pulse" / "概況"). On a phone, Pulse is the first bottom tab.
- Top-level component: `frontend/src/tabs/OverviewTab.tsx` — owns whether
  the concentration modal is open (`OpenCard` state); filtering comes from the
  shared `useScope`.

What the user sees/does:

- **Scope bar** — `frontend/src/components/scope/ScopeBar.tsx` shows the
  shared scope as a row of labelled conditions (see `analysis-tab.md`'s "The
  scope bar" section), leaving out of the row any condition
  `useOverviewSummary`'s `scope_applied` marks as unused.
- **First-visit tour** — `frontend/src/components/FirstRunTour.tsx`, three
  steps about meaning rather than controls: what one delay is (on the headline
  figure), what on time means (on the pointer to on-time % on Routes, worded
  from the on-time window the Routes link carries, since Pulse itself applies
  none) and what the screen counts (on the scope bar). `OverviewTab`
  mounts it inside the branch that renders the figures, so every anchor
  (`data-tour="pulse-*"`) exists whenever the tour does. Finishing it, ×, or
  Escape stores `transit.pulseTourSeen` (`frontend/src/api/tourSeen.ts`);
  "Later" closes it for that visit only.
- **Inline definitions** — `frontend/src/components/overview/MetricDefinition.tsx`
  puts an ⓘ popover (`InsightHint`) beside the average delay, each fact row
  (beside its control, never inside it) and the Routes to check heading: a one-
  or two-sentence definition consistent with the user manual, then a link to
  the manual section (`help.anchor.*`, per locale; `manualAnchors.test.tsx`
  ties each to a heading id).
- **Empty state** — `frontend/src/components/EmptyState.tsx` when the agency
  has zero samples, zero concentration routes, and an empty service split for
  the current range (`hasAnyData` in `OverviewTab.tsx`; `movers` and
  `peak_hour` are left out of that check — see the inline comments in
  `OverviewTab.tsx` for why).
- **Hero row** — `frontend/src/components/OverviewHeroRow.tsx`: the headline
  avg-delay over the scope period (through its last day with data) and its
  delta vs. the same number of days just before it, delayed-route count, and
  a sparkline, so the hero and the scope bar describe the same period.
  Every fact value, the delayed-route count included, prints in ink. The
  sparkline and the day-pulse ribbon under it are glances: a "See the trend on
  Time" link and the heaviest-hour fact row both open Time's `trend` report
  over the period's scope, where the trend and the day × hour views are drawn
  in full.
- **Trailing reference** — `frontend/src/components/overview/HeadlineReference.tsx`,
  under the headline figure: the headline's own figure over the 90 days
  ending the day before the period (`headline.reference`), under the same
  scope, as the agency's usual level, with a neutral chip judging the period
  better, about the same or worse. The chip takes the verdict's tolerance
  (`FLAT_MAX_DELTA_MIN` / `FLAT_MAX_DELTA_PCT` in
  `frontend/src/components/overview/storySentence.ts`: under 0.1 min or under
  5%), and judges nothing when either average is below 0 min, where a lower
  figure is no longer a better one. The baseline asks "did it change since
  just before?", the reference "is this usual for the agency?"; when the
  headline's window is 90 days wide the two are one window, and the line is
  left to the verdict.
  - The figure is withheld, with a stated reason, when the agency's feed has
    data on fewer than four fifths of the window's days the scope selects
    (`insufficient_days`: a new agency, or a long outage), when it rests on
    fewer than 100 observations (`insufficient_samples`), when nothing was
    observed (`no_data`), and under a time band with a routes filter
    (`not_aggregated`: no aggregate holds a route's departures by date and
    hour; decided before anything is read). Coverage counts the days the
    feed was collected, not the days the scope's routes ran.
  - A service filter is resolved over the reference window alone, so each of
    its dates is filed under the timetable the agency's calendar ran then;
    where the calendar changed its weekend timetables between the window and
    the period, a filter such as 土曜 can select a different set of day types
    in each.
  - The fact rows take no reference: their populations (the routes over a
    sample floor) and the concentration's spread change with the window's
    length, so a 90-day value is not the same statistic as the period's.
  - The window and the withholding rules live in
    `pipeline/reports/reference.py`, which each route's own reference on
    Routes and the route page reads as well (`analysis-tab.md` ▸ The Routes
    table ▸ Trailing reference).
- **Routes to check** — `frontend/src/components/overview/RoutesPreview.tsx`:
  the Routes table's own first five rows (`route_table`, sorted by average
  delay, worst first), drawn by `ReportTable` over the headline's window and
  the screen's filters, and a link to the Routes table with that same scope
  and sort. The preview and the table are one request shape, so they list
  the same routes with the same figures. The Routes table is the home of
  "which routes are worst" (`docs/features/analysis-tab.md` ▸ The Routes
  table).
- **Route names on one column** — the concentration bar's rows share one CSS
  grid (`.ov-pareto-list`) passed down as subgrids to `RouteLabel`'s
  `plateColumn` form, so the plate track is as wide as the list's widest
  plate and every name starts on one column whether or not its route has a
  plate. On a phone the rows put the name on its own line, the bar and share
  beneath it. The build's default browser target predates subgrid in
  Chromium, so an `@supports not (grid-template-columns: subgrid)` block
  gives every row back its own columns there.
- **Module sections** — up to three modules, each rendered inline and
  always visible when it has data, wrapped in
  `frontend/src/components/overview/RevealSection.tsx`, which animates it
  in as it scrolls into view:
  - `frontend/src/components/ConcentrationBar.tsx` — Pareto-style "which
    routes account for most of the delay" bar (card shows 5; clicking it
    opens `frontend/src/components/OverviewModal.tsx` with the top 20 and a
    Lorenz-curve overlay), plus the worsening/improving movers list. It
    ranks routes by their share of the period's total delay, which the
    Routes table has no sort for, so it stays here rather than linking. Its
    bars are one neutral tone: they show a share of the delay, not minutes,
    so the delay ramp stays off them.
  - `frontend/src/components/PeakHourSummary.tsx` — the heaviest hour of the
    service day as one sentence, and a link to Time's `trend` report over the
    period's scope, the home of the day × hour question. Pulse draws no hour
    chart of its own.
  - `frontend/src/components/ServiceSplit.tsx` — one sentence comparing the
    period's day types (slowest against fastest), each day type's mean on a
    line under it ("not enough data" or "no data" where it has none), and a
    link to Day types & agencies (`compare?by=periods`) over the period's scope, the home of the
    weekday-against-weekend question, compared route by route. Pulse draws no
    day-type chart of its own. Each date is filed under the
    timetable class its agency's calendar runs that day
    (`pipeline/day_types.py`: `calendar.txt` weekly flags plus
    `calendar_dates.txt` exceptions, majority by trip count; Saturday /
    Sunday-or-public-holiday / weekday where the calendar is silent), never
    under the observation's own `service_type`. A feed with one Saturday+Sunday
    timetable is split 平日 / 土日祝, every other 平日 / 土曜 / 日祝; a day type
    under 3 days or 1,000 samples is flagged `insufficient` and left out of the
    comparison. The day types are resolved once per payload, over the range
    plus the days before it that the baseline (as wide as the range) and the
    movers' four-week sparkline read, and the service filter uses the same
    resolution, so where a feed changes its weekend timetables that whole span
    decides the split. The 90-day reference resolves its own window instead,
    so reaching back for it never moves the period's split.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useOverviewSummary(agencyId, ctx)` | `GET /api/{agency_id}/overview/summary` (`api/routers/overview.py: overview_summary`) | `pipeline/reports/overview.py: compute_overview_summary()` — headline, movers, concentration, the late-route count (`top_delayed`), and service-split each fast-path off precomputed `agg_daily_trend` when `ctx.time_band == "all"`, and a `time_band`-narrowed request derives every figure from one shared grain read from `agg_route_band_daily` (each route's departures per day and band) and `agg_hour_daily` (`_fetch_band_aggregate`), with no ClickHouse scan of `updates`, and the same figures the scan gives (module docstring at the top of `pipeline/reports/overview.py`). The hour profile (`peak_hour`) comes from `_peak_hour_by_dow()` over the range: the per-day `agg_hour_daily`, filtering dates by day-of-week and by the service filter's dates, and a live scan only under a `routes` filter, since that table pools every route. Under a `routes` filter that scan is the request's only ClickHouse work, so a failure degrades just that field to null. The grain is a live ClickHouse scan instead only for a band whose edges fall inside an hour, which `agg_hour_daily` cannot split (`_band_aggregate_answers`; no band has one), and for an agency analyze has not yet built `agg_route_band_daily` for (`_band_aggregate_built`, read from `agg_table_state`), whose empty rows would otherwise read as a band with no departures. Such a scan past the ClickHouse client's row cap or its time budget is fetched again over the range's routes alone, and the fields that need the days before the range (the baseline and its deltas, `movers`) and the hour profile degrade to null. `headline.reference` reads Postgres alone (`_reference`): `agg_daily_trend` through the headline's own `_headline_stats`, or `agg_hour_daily` under a time band (an hour taken modulo 24 against the band's whole-hour edges), plus one count of the agency's collected days; it never scans `updates`, so a degraded grain leaves it standing. |
| `useReport(agencyId, "route_table", …)` (`RoutesPreview`) | `GET /api/{agency_id}/reports/route_table?sort=avg&limit=5` | The Routes table's own report over the headline's window (`analysis-tab.md` ▸ The Routes table). It is requested once the summary has answered, since the window comes from it. Under a time band, or an on-time tolerance in the histogram's open end buckets, it is a live ClickHouse scan (`compute_route_table`), whose cached result the Routes table reuses for the same scope. The summary's `top_delayed.route_count` leaves out what this report leaves out over the same window (`_top_delayed`), so the hero's "N / M routes" and the preview's "Covers M routes" are one population. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/OverviewTab.tsx` | Overview tab shell: modal-open state, empty-state gating, the scoped links to Routes, Time and Day types & agencies |
| `frontend/src/components/OverviewHeroRow.tsx` | Headline delta + sparkline + delayed-route count |
| `frontend/src/components/overview/HeadlineReference.tsx` | The headline's trailing 90-day reference line, its judgement chip and hint |
| `frontend/src/components/overview/RoutesPreview.tsx` | The Routes table's first five rows and the link to the table |
| `frontend/src/components/ConcentrationBar.tsx` | Pareto bar (card/modal variants) + movers list |
| `frontend/src/components/PeakHourSummary.tsx` | The heaviest hour and the link to Time |
| `frontend/src/components/ServiceSplit.tsx` | The day-type sentence, each day type's mean, and the link to Day types & agencies |
| `frontend/src/components/OverviewModal.tsx` | Modal chrome the concentration card opens into |
| `frontend/src/components/overview/RevealSection.tsx` | Scroll-triggered, staggered entrance for each module section; content stays visible at rest |
| `frontend/src/components/scope/ScopeBar.tsx` | The scope bar, its popovers and pinned strip |
| `frontend/src/api/hooks.ts` | `useOverviewSummary`, `useReport` |
| `frontend/src/styles/overview.css` | Overview-specific layout/spacing |

**Backend**

| File | Role |
|---|---|
| `api/routers/overview.py` | `GET /overview/summary` |
| `pipeline/reports/overview.py` | `compute_overview_summary()` and its per-module helpers (headline and its reference, movers, concentration, the late-route count, peak-hour, service-split); fast-path-vs-live-scan pattern documented in the module docstring |
| `pipeline/analyze.py`, `pipeline/ch_aggregates.py` | Build `agg_daily_trend` and `agg_hour_daily` — the aggregates this tab reads. `ch_aggregates` computes them in ClickHouse; `analyze` copies them into Postgres |
| `pipeline/day_types.py` | Files each service date under a day type from the agency's `static_calendar` / `static_calendar_dates` (loaded by `pipeline/static_loader.py`), with the weekday rule as fallback |

## How to verify manually

**Automated tests:**

- Frontend: `frontend/src/tabs/OverviewTab.test.tsx`,
  `frontend/src/components/OverviewHeroRow.test.tsx`,
  `frontend/src/components/overview/HeadlineReference.test.tsx`,
  `frontend/src/components/overview/storySentence.test.ts`,
  `frontend/src/components/ServiceSplit.test.tsx`.
- Backend: `tests/api/test_overview.py` (`/overview/summary`),
  `tests/api/test_overview_reference.py` (the
  reference's window, its parity with the headline, its reasons),
  `tests/unit/test_day_types.py` (day-type resolution).

**Manual click-through** (`make serve` + `make frontend-dev`, or `make serve`
alone for single-origin):

1. `make bootstrap && make serve` (+ `make frontend-dev` for hot reload on
   `:5173`). `make bootstrap` alone leaves the DB empty — load data first:
   `make fetch-ingest` (or `make load_static` before `ingest_live`), then
   `make analyze` for the agency.
2. Open the app — a fresh/remembered agency selection and a bare
   `/agencies/{id}` both land on Pulse (`/agencies/{id}/pulse`); from
   elsewhere, click "Pulse" in the rail.
3. Expect the hero row (headline delta + sparkline) and a "Routes to check"
   preview once data exists; with no data yet, expect the empty state instead.
   Below them, expect the concentration bar, the heaviest-hour card and the
   day-type card already visible.
4. Click the concentration bar card — expect `OverviewModal` to open with
   the larger variant.
5. Follow "See all … routes", "See every hour by day on Time", "See the trend
   on Time" and the day-type card's link — expect each destination to show
   the same period and filters, and the Routes table to open with the
   preview's routes and figures at its top.
6. Change the scope bar's period / days / time / routes —
   expect every module to refetch via `useOverviewSummary`.

## i18n

- Frontend strings live under the `overview.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus `nav.pulse` for the rail entry, and the shared
  `scope.*` namespace used by the scope bar.
- No server-side `_LOCALES` strings for this tab — `overview_summary` takes
  no locale, since nothing in its payload is worded on the server
  (`api/routers/overview.py`'s docstring); the payload is numeric/string
  data the frontend renders entirely through its own `t()` calls.
