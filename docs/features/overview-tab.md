# Overview tab

Magazine-style "how's the agency doing" landing page: one round-trip returns
a headline delta, a concentration/movers module, a peak-hour ribbon, and a
weekday-vs-weekend split, each expandable into a bigger modal view.

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
- Top-level component: `frontend/src/tabs/OverviewTab.tsx` — owns which
  module's modal is open (`OpenCard` state) and the peak-hour-breakdown
  drill-down selection; filtering comes from the shared `useScope`.

What the user sees/does:

- **Scope sentence** — `frontend/src/components/scope/ScopeSentence.tsx`
  states the shared scope as one sentence (see `analysis-tab.md` ▸ Scope),
  greying the conditions `useOverviewSummary`'s `scope_applied` did not apply.
- **Empty state** — `frontend/src/components/EmptyState.tsx` when the agency
  has zero samples, zero concentration routes, and an empty service split for
  the current range (`hasAnyData` in `OverviewTab.tsx`; `movers` and
  `peak_hour` are left out of that check — see the inline comments in
  `OverviewTab.tsx` for why).
- **Hero row** — `frontend/src/components/OverviewHeroRow.tsx`: the headline
  avg-delay delta vs. a prior-week baseline, delayed-route count, and a
  sparkline.
- **Routes to check** — `frontend/src/components/RoutesToCheckList.tsx`: the
  top-5 routes by absolute avg delay over the same 7-day window as the
  headline.
- **Module sections** — up to three modules, each rendered inline and
  always visible when it has data, wrapped in
  `frontend/src/components/overview/RevealSection.tsx`, which animates it
  in as it scrolls into view. Clicking a module's card opens a bigger
  `frontend/src/components/OverviewModal.tsx` view:
  - `frontend/src/components/ConcentrationBar.tsx` — Pareto-style "which
    routes account for most of the delay" bar (card shows 5, modal shows the
    top 20 + a Lorenz-curve overlay), plus the worsening/improving movers list.
  - `frontend/src/components/PeakHourRibbon.tsx` — 24-hour avg-delay ribbon;
    clicking an hour bar that has data opens
    `frontend/src/components/PeakHourModal.tsx` with the top routes for that
    hour (optionally split by day-of-week) instead of the card's
    `OverviewModal`.
  - `frontend/src/components/ServiceSplit.tsx` — avg delay per day type
    (card) or a per-day history (modal). Each date is filed under the
    timetable class its agency's calendar runs that day
    (`pipeline/day_types.py`: `calendar.txt` weekly flags plus
    `calendar_dates.txt` exceptions, majority by trip count; Saturday /
    Sunday-or-public-holiday / weekday where the calendar is silent), never
    under the observation's own `service_type`. A feed with one Saturday+Sunday
    timetable is split 平日 / 土日祝, every other 平日 / 土曜 / 日祝; a day type
    under 3 days or 1,000 samples is flagged `insufficient` and left out of the
    comparison. The day types are resolved once per payload, over the range
    plus the four weeks before it that the movers and baseline read, and the
    service filter uses the same resolution, so where a feed changes its
    weekend timetables that whole span decides the split.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useOverviewSummary(agencyId, ctx)` | `GET /api/{agency_id}/overview/summary` (`api/routers/overview.py: overview_summary`) | `pipeline/reports/overview.py: compute_overview_summary()` — headline, movers, concentration, top-delayed, and service-split each fast-path off precomputed `agg_daily_trend` when `ctx.time_band == "all"`, falling back to a live ClickHouse scan of `updates` only for a `time_band`-narrowed request (module docstring at the top of `pipeline/reports/overview.py`). The three hour profiles (`peak_hour` and the modal's weekday/weekend split) all come from `_peak_hour_by_dow()` over the range: the per-day `agg_hour_daily`, filtering dates by day-of-week and by the service filter's dates, and the shared live scan only under a `routes` or `time_band` filter. Under a `routes` filter alone that scan is the request's only ClickHouse work, so a failure degrades just those three fields to null. |
| `usePeakHourBreakdown(agencyId, hour, dow)` (fires only once an hour is clicked on the ribbon) | `GET /api/{agency_id}/peak-hour-breakdown` (`api/routers/overview.py: peak_hour_breakdown`) | `agg_route_hour_dow`, one row per route pooled across its raw service ids, and across DOWs when `dow` is omitted; routes with fewer than 3 samples are excluded. Returns up to 20 routes, worst avg delay first. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/OverviewTab.tsx` | Overview tab shell: modal-open state, peak-hour drill-down selection, empty-state gating |
| `frontend/src/components/OverviewHeroRow.tsx` | Headline delta + sparkline + delayed-route count |
| `frontend/src/components/RoutesToCheckList.tsx` | Top-5 routes by absolute avg delay |
| `frontend/src/components/ConcentrationBar.tsx` | Pareto bar (card/modal variants) + movers list |
| `frontend/src/components/PeakHourRibbon.tsx` | 24-hour avg-delay ribbon (card/modal variants) |
| `frontend/src/components/PeakHourModal.tsx` | Per-hour (optionally per-DOW) top-routes drill-down |
| `frontend/src/components/ServiceSplit.tsx` | Day-type split (card/modal variants) |
| `frontend/src/components/OverviewModal.tsx` | Shared modal chrome each expandable card opens into |
| `frontend/src/components/overview/RevealSection.tsx` | Scroll-triggered, staggered entrance for each module section; content stays visible at rest |
| `frontend/src/components/scope/ScopeSentence.tsx` | The scope sentence, its popovers and pinned strip |
| `frontend/src/api/hooks.ts` | `useOverviewSummary`, `usePeakHourBreakdown` |
| `frontend/src/styles/overview.css` | Overview-specific layout/spacing |

**Backend**

| File | Role |
|---|---|
| `api/routers/overview.py` | `GET /overview/summary`, `GET /peak-hour-breakdown` |
| `pipeline/reports/overview.py` | `compute_overview_summary()` and its per-module helpers (headline, movers, concentration, top-delayed, peak-hour, service-split); fast-path-vs-live-scan pattern documented in the module docstring |
| `pipeline/analyze.py`, `pipeline/ch_aggregates.py` | Build `agg_daily_trend`, `agg_hour_daily`, `agg_route_hour_dow` — the aggregates this tab reads. `ch_aggregates` computes them in ClickHouse; `analyze` copies them into Postgres |
| `pipeline/day_types.py` | Files each service date under a day type from the agency's `static_calendar` / `static_calendar_dates` (loaded by `pipeline/static_loader.py`), with the weekday rule as fallback |

## How to verify manually

**Automated tests:**

- Frontend: `frontend/src/tabs/OverviewTab.test.tsx`,
  `frontend/src/components/OverviewHeroRow.test.tsx`,
  `frontend/src/components/PeakHourModal.test.tsx`,
  `frontend/src/components/RoutesToCheckList.test.tsx`,
  `frontend/src/components/routesToCheckBands.test.ts`.
- Backend: `tests/api/test_overview.py` (`/overview/summary`,
  `/peak-hour-breakdown`), `tests/unit/test_day_types.py` (day-type
  resolution).

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
   list once data exists; with no data yet, expect the empty state instead.
   Below them, expect the concentration bar, peak-hour ribbon, and service
   split already visible.
4. Click the concentration bar card, the service split card, or the
   peak-hour card outside an hour bar — expect `OverviewModal` to open with
   the larger variant of that module.
5. Click an individual hour bar on the peak-hour ribbon — expect
   `PeakHourModal` to open with that hour's top routes.
6. Change the scope sentence's period / days / time / routes —
   expect every module to refetch via `useOverviewSummary`.

## i18n

- Frontend strings live under the `overview.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus `nav.pulse` for the rail entry, and the shared
  `scope.*` namespace used by the scope sentence.
- No server-side `_LOCALES` strings for this tab — `overview_summary` takes
  no locale, since nothing in its payload is worded on the server
  (`api/routers/overview.py`'s docstring); the payload is numeric/string
  data the frontend renders entirely through its own `t()` calls.
