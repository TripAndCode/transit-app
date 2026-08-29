# Overview tab

Magazine-style "how's the agency doing" landing page: one round-trip returns
a headline delta, a concentration/movers module, a peak-hour ribbon, and a
weekday-vs-weekend split, each expandable into a bigger modal view.

## How a user reaches it

- Route: `/agencies/:agencyId/overview`, registered in `frontend/src/main.tsx`
  (`React.lazy`-loaded). It **is** the default landing tab — a bare
  `agencies/:agencyId` navigates here via `<Navigate to="overview" replace />`
  (`frontend/src/main.tsx`). A fresh/remembered agency selection instead
  redirects to Map (`frontend/src/components/OnboardingGate.tsx`), so Overview
  is reached either by that default redirect or by clicking "Overview" in the
  sidebar.
- Sidebar nav link: `frontend/src/components/Sidebar.tsx` (`nav.overview` i18n
  key, first entry in `ITEMS`).
- Top-level component: `frontend/src/tabs/OverviewTab.tsx` — owns which
  module's modal is open (`OpenCard` state) and the peak-hour-breakdown
  drill-down selection; filtering comes from the shared `useRangeContext`.

What the user sees/does:

- **Filter bar** — `frontend/src/components/TabFilterBar.tsx`, the same
  shared dow/service/time_band/route filter used by every tab.
- **Empty state** — `frontend/src/components/EmptyState.tsx` when the agency
  has zero samples, zero concentration routes, and an empty service split for
  the current range (`hasAnyData` in `OverviewTab.tsx`; `movers` and
  `peak_hour` are deliberately excluded from that check — see the inline
  comments in `OverviewTab.tsx` for why each would give a false positive).
- **Hero row** — `frontend/src/components/OverviewHeroRow.tsx`: the headline
  avg-delay delta vs. a prior-week baseline, delayed-route count, and a
  sparkline.
- **Routes to check** — `frontend/src/components/RoutesToCheckList.tsx`: the
  top-5 routes by absolute avg delay over the same 7-day window as the
  headline.
- **Details toggle** (a native `<details>`) reveals up to three collapsed
  modules, each clickable to open a bigger `frontend/src/components/OverviewModal.tsx`
  view:
  - `frontend/src/components/ConcentrationBar.tsx` — Pareto-style "which
    routes account for most of the delay" bar (card shows 5, modal shows the
    top 20 + a Lorenz-curve overlay), plus the worsening/improving movers list.
  - `frontend/src/components/PeakHourRibbon.tsx` — 24-hour avg-delay ribbon;
    clicking an hour opens `frontend/src/components/PeakHourModal.tsx` with
    the top routes for that hour (optionally split by day-of-week).
  - `frontend/src/components/ServiceSplit.tsx` — weekday vs. weekend avg
    delay split (card) or a per-day history (modal).

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useOverviewSummary(agencyId, ctx)` | `GET /api/{agency_id}/overview/summary` (`api/routers/overview.py: overview_summary`) | `pipeline/reports/overview.py: compute_overview_summary()` — headline, movers, concentration, top-delayed, and service-split each fast-path off precomputed `agg_daily_trend` when `ctx.time_band == "all"`, falling back to a live ClickHouse scan of `updates` only for a `time_band`-narrowed request (module docstring at the top of `pipeline/reports/overview.py`). **`peak_hour` is the one exception**: it always reads `agg_route_hour` (a fixed analyze-period rollup with no date column) regardless of `ctx`, so it can never reflect "no data in this range" — `OverviewTab.tsx` deliberately excludes it from the empty-state check for this reason. `peak_hour_by_dow` (used for the modal's weekday/weekend split) instead reads the per-day `agg_hour_daily`, filtering dates by day-of-week. |
| `usePeakHourBreakdown(agencyId, hour, dow)` (fires only once an hour is clicked on the ribbon) | `GET /api/{agency_id}/peak-hour-breakdown` (`api/routers/overview.py: peak_hour_breakdown`) | `agg_route_hour_dow`, pooled across DOWs when `dow` is omitted; routes with fewer than 3 samples are excluded. Returns up to 20 routes, worst avg delay first. |

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
| `frontend/src/components/ServiceSplit.tsx` | Weekday vs. weekend split (card/modal variants) |
| `frontend/src/components/OverviewModal.tsx` | Shared modal chrome each expandable card opens into |
| `frontend/src/components/TabFilterBar.tsx` | Shared dow/service/time_band/route filter UI |
| `frontend/src/api/hooks.ts` | `useOverviewSummary`, `usePeakHourBreakdown` |
| `frontend/src/styles/overview.css` | Overview-specific layout/spacing |

**Backend**

| File | Role |
|---|---|
| `api/routers/overview.py` | `GET /overview/summary`, `GET /peak-hour-breakdown` |
| `pipeline/reports/overview.py` | `compute_overview_summary()` and its per-module helpers (headline, movers, concentration, top-delayed, peak-hour, service-split); fast-path-vs-live-scan pattern documented in the module docstring |
| `pipeline/analyze.py` | Builds `agg_daily_trend`, `agg_route_hour`, `agg_hour_daily`, `agg_route_hour_dow` — the aggregates this tab reads |

## How to verify manually

**Automated tests:**

- Frontend: `frontend/src/tabs/OverviewTab.test.tsx`,
  `frontend/src/components/OverviewHeroRow.test.tsx`,
  `frontend/src/components/PeakHourModal.test.tsx`,
  `frontend/src/components/RoutesToCheckList.test.tsx`,
  `frontend/src/components/routesToCheckBands.test.ts`.
- Backend: `tests/api/test_overview.py` (`/overview/summary`,
  `/peak-hour-breakdown`).

**Manual click-through** (`make serve` + `make frontend-dev`, or `make serve`
alone for single-origin):

1. `make bootstrap && make serve` (+ `make frontend-dev` for hot reload on
   `:5173`). `make bootstrap` alone leaves the DB empty — load data first:
   `make fetch-ingest` (or `ingest_live` + `make load_static`), then
   `make analyze` for the agency.
2. Open the app — a fresh/remembered agency selection lands on Map, but a
   bare `/agencies/{id}` (or a freshly picked agency without a remembered
   tab) lands on Overview by default; otherwise click "Overview" in the
   sidebar.
3. Expect the hero row (headline delta + sparkline) and a "Routes to check"
   list once data exists; with no data yet, expect the empty state instead.
4. Click the details `<summary>` toggle — expect the concentration bar, peak
   hour ribbon, and service split to expand.
5. Click the concentration bar, the service split, or a bar on the peak-hour
   ribbon — expect `OverviewModal` to open with the larger variant of that
   module.
6. Click an individual hour on the peak-hour ribbon — expect `PeakHourModal`
   to open with that hour's top routes.
7. Change the filter bar's date range / dow / time_band / route selection —
   expect every module to refetch via `useOverviewSummary`.

## i18n

- Frontend strings live under the `overview.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), plus `nav.overview` / `nav.overview_subtitle` for the
  sidebar entry and the shared `filters.*` namespace used by `TabFilterBar`.
- No server-side `_LOCALES` strings for this tab — `overview_summary`'s
  `locale` parameter is reserved for future qualitative labels
  (`api/routers/overview.py`'s docstring); today's payload is numeric/string
  data the frontend renders entirely through its own `t()` calls.
