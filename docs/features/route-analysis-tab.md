# Route analysis tab

Single-route segment workspace: a stop-by-stop mean-delay chart, a small map,
an optional previous-week comparison, filtered CSV export, and browser-local
saved-analysis bookmarks — scoped to exactly one selected route.

## How a user reaches it

- Route: `/agencies/:agencyId/route-analysis`, registered in
  `frontend/src/main.tsx` (`React.lazy`-loaded).
- Sidebar nav link: `frontend/src/components/Sidebar.tsx`'s
  `SIDEBAR_NAV_ITEMS` (`route-analysis` entry, labeled from the `design`
  i18n namespace's `analysis` key — "Segment analysis" / "区間分析").
- Top-level component: `frontend/src/tabs/RouteAnalysisTab.tsx` — owns the
  compare-with-previous-week toggle (`?compare=1` search param), the
  selected stop, and which of the three sub-tabs (trend / map / by-stop) is
  active.

What the user sees/does:

- **Route/keito filter** — `frontend/src/components/analysis/AnalysisFilters.tsx`.
  With no single route selected (`ctx.routes.length !== 1`) the tab shows an
  `EmptyState` prompting the user to choose one instead of rendering data.
- **Header actions** — a CSV download button (disabled while loading, on
  error, or while a requested comparison is still fetching) and a "Save this
  analysis" button that writes a browser-local bookmark
  (`frontend/src/components/analysis/savedAnalyses.ts`).
- **Compare checkbox** — when checked, fetches the same route's stop shape
  for the same weekday range shifted 7 days earlier
  (`isoDaysBefore(ctx.from/to, 7)`) and overlays it on the chart; matching is
  by stop id and sequence, so a stop missing from either window is a gap, not
  an interpolated value.
- **Three sub-tabs**: Trend (`frontend/src/components/analysis/StopChart.tsx`
  — current vs. previous-week series, selectable points), Map
  (`frontend/src/components/analysis/AnalysisMap.tsx`, mounted only after
  first visited, hidden rather than unmounted afterward), and By stop (a
  plain stop/mean/samples table).
- **Selected-stop aside** — a `<select>` of every stop plus the selected
  stop's mean delay and sample count.
- A caveat line states these are per-stop means for the representative
  observed pattern, not evidence of an individual trip's delay growth or its
  cause.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useRouteShape(agencyId, route, ctx)` (current window; a second call with `ctx` shifted 7 days earlier fires only when the compare checkbox is on) | `GET /api/{agency_id}/route-shape?route=...` (`api/routers/map.py: route_shape`) | `pipeline/reports/map.py: compute_route_shape()` — a cheap Postgres `agg_route_daily` existence precheck, then a live ClickHouse dedup scan of `updates` to vote the route's most-frequent `shape_id` and compute per-stop mean departure delay, joined to Postgres `static_stops`/`static_shapes` for geometry and stop labels. No precomputed-aggregate fast path exists for this endpoint — every request scans ClickHouse live. |

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/RouteAnalysisTab.tsx` | Tab shell: compare toggle, stop selection, sub-tab state |
| `frontend/src/components/analysis/AnalysisFilters.tsx` | Route/keito filter UI |
| `frontend/src/components/analysis/StopChart.tsx` | Per-stop delay chart (current + optional previous-week overlay) |
| `frontend/src/components/analysis/AnalysisMap.tsx` | Small map view of the selected route's stops |
| `frontend/src/components/analysis/stopSeries.ts` | `orderedStops()` / `matchedPrevious()` — stop ordering and week-over-week matching |
| `frontend/src/components/analysis/savedAnalyses.ts` | Browser-local saved-analysis read/write/delete |
| `frontend/src/components/analysis/csv.ts` | `downloadCsv()` shared by every analysis/report screen |
| `frontend/src/api/hooks.ts` | `useRouteShape` |

**Backend**

| File | Role |
|---|---|
| `api/routers/map.py` | `GET /route-shape` |
| `pipeline/reports/map.py` | `compute_route_shape()`, `route_exists()` |

## How to verify manually

**Automated tests:**

- Frontend: `frontend/src/components/analysis/workflows.test.tsx` (renders
  `RouteAnalysisTab` and `ReportsHomeTab` together on their real routes),
  `frontend/src/components/analysis/StopChart.test.ts`.
- Backend: `tests/api/test_api_map.py`, `tests/unit/test_range_updates_filter_ch.py`,
  `tests/unit/test_response_schema_ratchet.py` (all exercise `/route-shape`
  alongside the Map tab's other endpoints in `api/routers/map.py`).

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load and analyze
   data first: `make fetch-ingest` (or `ingest_live` + `make load_static`),
   then `make analyze` for the agency.
2. Click "Segment analysis" in the sidebar → URL
   `/agencies/:agencyId/route-analysis`; expect the "choose a route" empty
   state until exactly one route is selected in the filter.
3. Select one route — expect the stop chart, map, and by-stop table to
   populate; switch between the three sub-tabs.
4. Toggle "Compare with one week earlier" — expect a second series on the
   chart, or a "no comparison data" message if the prior week has none.
5. Click a stop in the chart or the aside's dropdown — expect the selection
   to sync across the chart and the aside's delay/sample readout.
6. Click "Save this analysis" — expect a saved-confirmation notice; reload
   `ReportsHomeTab`'s saved-analyses view to find the bookmark listed.
7. Click the CSV download button — expect a file with per-stop rows plus a
   comparison-window footer when compare is on.

## i18n

- Frontend strings come from the `design` i18n namespace
  (`frontend/src/i18n/design.ts`, loaded via `useTranslation("design")`), not
  the default `translation` namespace's `frontend/src/i18n/locales/{ja,en}.json`
  files. `design.ts` holds its own `ja`/`en` objects directly in the TS
  module, and neither i18n gate reaches it: `npm run lint:i18n`'s key-parity
  check reads only the two `locales/*.json` files, and
  `npm run lint:i18n-strings` skips `src/i18n/design.ts` by name. A new
  `design.ts` key needs its `ja`/`en` pair kept in sync by hand.
