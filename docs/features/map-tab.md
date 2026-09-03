# Map tab

Agency-wide delay heatmap plus a single-route drill-down, rendered with
MapLibre GL over free GSI basemaps. See the `maplibre-map` skill
(`.claude/skills/maplibre-map/SKILL.md`) for the map-rendering conventions
this doc's "key files" section builds on.

## How a user reaches it

- Sidebar nav item "Map" (`nav.map` i18n key) → route
  `/agencies/:agencyId/map`, registered in `frontend/src/main.tsx`
  (`MapTab` is `React.lazy`-loaded specifically to keep MapLibre, ~800 KB,
  out of the entry chunk).
- Top-level component: `frontend/src/tabs/MapTab.tsx`.

Controls on the tab:

- **`TabFilterBar`** (`frontend/src/components/TabFilterBar.tsx`) — shared
  filter popover for `dow`, `service` (平日/土日祝), `time_band` (morning
  / forenoon / noon / afternoon / evening / night / late_night), and route
  multi-select (`RoutesPicker`). State lives in the URL query string via
  `frontend/src/api/rangeContext.ts` (`useRangeContext`/`RangeCtx`), so
  filters are shareable/bookmarkable.
- **Basemap switcher**: `frontend/src/tabs/map/MapStyleControl.tsx` — a
  "Layers" pill (bottom-left) with 4 GSI/OSM options (OSM / 淡色 pale /
  標準 std / 航空写真 photo), preference persisted via
  `frontend/src/tabs/map/useMapStylePref.ts` (localStorage), tile catalog
  defined in `frontend/src/styles/mapStyle.ts` (`MAP_STYLES`,
  `buildStyle()`).
- **Heatmap metric toggle** (avg vs p90): inline button in `MapTab.tsx`
  toggling `heatmapField` between `avg_delay_min`/`p90_delay_min`
  (`map.heatmapMode.*` i18n keys).
- **Legend / severity filter**:
  `frontend/src/components/MapLegend.tsx` — click a severity swatch to
  filter the map to that band; also a "show single-sample stops"
  checkbox.
- **Route drill-down controls** (only shown when exactly one route is
  selected, `ctx.routes.length === 1`):
  `frontend/src/tabs/map/RouteModeToggle.tsx` (trend vs hourly line
  coloring) and `frontend/src/tabs/map/MapHourScrubber.tsx` (hour slider +
  play/pause, drives "hourly" mode's expected-delay coloring via
  `frontend/src/tabs/map/expectedDelay.ts`).
- Map interactions: click a stop dot / cluster / route-stop for a popup
  (`frontend/src/components/MapPopupHTML.ts`); hover for feature-state
  highlight; click a cluster bubble to zoom to expansion.

There is no dedicated "POI layer" component in this repo — the only
overlays besides the heatmap and route line are the basemap itself and
`useBasemapDim` (a desaturation scrim, not a POI feature). The
`maplibre-map` skill's "POI names exist in dual casing" gotcha refers to
basemap-native/stop-name label matching, not a bespoke POI layer file.

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useHeatmap(agencyId, ctx)` | `GET /api/{agency_id}/delays/heatmap` (`api/routers/map.py`) | **Always precomputed Postgres aggregates**, even for a `time_band`-narrowed request — no route filter reads `agg_stop_daily` (+ `agg_stop_routes` for labels); a route filter reads `agg_route_stop_daily`. Both are pre-bucketed by `date`/`service_type`/`time_band`. Stops are merged via `ST_ClusterDBSCAN` in Postgres/PostGIS. This is a documented exception to the repo-wide "narrow request falls back to ClickHouse" rule — see the docstring in `api/routers/map.py` and the `maplibre-map` skill. |
| `useRouteShape(agencyId, route, ctx)` (fires only when exactly one route is selected) | `GET /api/{agency_id}/route-shape` (`api/routers/map.py`) | **Live ClickHouse scan** of `updates` (via `build_updates_filter_ch(ctx)`), bounded by the ctx date/dow/time_band/service window, joined to Postgres `static_shapes`/`static_stops`/`static_trips` for geometry. This is the Map tab's "narrow request → live ClickHouse" case. Falls back to a 30-day-bounded unfiltered vote when the ctx window has zero observations. |
| `useForecastHeatmap(agencyId, route)` (fires whenever exactly one route is selected — `enabled: agencyId != null && !!route` — in both "trend" and "hourly" drill-down mode; only its *consumption*, the expected-delay coloring/scrubber, is hourly-mode-gated in `MapTab.tsx`) | `GET /api/{agency_id}/forecast/heatmap` (`api/routers/reports.py`, not `map.py`) | Precomputed `agg_route_hour_dow` (day-of-week × scheduled-hour grid) — no live scan. |
| (not called from `MapTab`, same router file) `GET /today/route-summary`, `/today/route/{code}/trips`, `/today/route/{code}/stop-profile` | `api/routers/map.py` | Power the separate "最新観測" (Live) triage tab (`frontend/src/tabs/LiveTab.tsx` / `frontend/src/tabs/live/RouteDrilldown.tsx` via `useTodayRouteSummary`/`useRouteTrips`/`useRouteStopProfile`); the two `today/route/{code}/*` drilldowns run live bounded ClickHouse scans, `today/route-summary` reads precomputed `agg_route_daily`/`agg_route_stats`/`agg_feed_health`. Included here only because they share `map.py`. |
| (not called from any current frontend file) `GET /delays/live` | `api/routers/map.py` | Has no current frontend consumer — only referenced from `tests/api/test_api_map.py`. Listed here only because it lives in the same router file; don't assume it backs the Live tab. |

Key backend building block: `api/range.py` — `get_range_ctx` (parses
`from/to/dow/time_band/service/routes` query params into `RangeCtx`),
`build_agg_stop_filter` (Postgres agg WHERE clause), and
`build_updates_filter_ch` (ClickHouse WHERE clause) implement the
agg-vs-live-ClickHouse filter-fragment split described in `CLAUDE.md`.

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/MapTab.tsx` | Orchestrator; owns the MapLibre `Map` instance, click/hover handlers via `useEffectEvent` (fresh `ctx`/`t` in stable handlers, per `CLAUDE.md`), style-switch effect, composes the hooks/components below |
| `frontend/src/tabs/map/MapStyleControl.tsx` | Basemap switcher UI |
| `frontend/src/styles/mapStyle.ts` | GSI/OSM tile catalog, `buildStyle()`, style-pref read/write, `VITE_MAP_STYLE_URL` env override |
| `frontend/src/tabs/map/useMapStylePref.ts` | Persisted style-id state hook |
| `frontend/src/tabs/map/useHeatmapLayer.ts` | Builds/updates the clustered GeoJSON source + circle/cluster/count layers (`SOURCE="delays"`, `LAYER="delay-circles"`) |
| `frontend/src/tabs/map/useRouteOverlay.ts` | Single-route polyline (trend/hourly coloring) + numbered stop dots; hides/shows the agency-wide heatmap layers |
| `frontend/src/tabs/map/routeTrendSegments.ts` | Builds per-segment trend-line GeoJSON for `useRouteOverlay` |
| `frontend/src/tabs/map/useBasemapDim.ts` | Desaturates the raster basemap + adds a scrim so overlays pop |
| `frontend/src/tabs/map/styleReady.ts` | `whenStyleReady()` — the layer re-attach helper called out in the `maplibre-map` skill (do not rely on `isStyleLoaded()`/one-shot `style.load` alone) |
| `frontend/src/tabs/map/MapHourScrubber.tsx`, `RouteModeToggle.tsx`, `expectedDelay.ts` | Hourly drill-down controls/logic |
| `frontend/src/components/MapLegend.tsx` | Legend/severity filter/single-sample toggle |
| `frontend/src/components/MapPopupHTML.ts` | Popup HTML template for stop-dot and route-stop click handlers |
| `frontend/src/components/TabFilterBar.tsx` | Shared dow/service/time_band/route filter UI |
| `frontend/src/api/rangeContext.ts` | URL-persisted filter state (`RangeCtx`, `useRangeContext`) |
| `frontend/src/api/hooks.ts` | `useHeatmap`, `useRouteShape`, `useForecastHeatmap` |

**Backend**

| File | Role |
|---|---|
| `api/routers/map.py` | `/route-shape` and `/delays/heatmap` back the Map tab; `/today/route-summary`, `/today/route/{route_code}/trips`, `/today/route/{route_code}/stop-profile` back the separate Live tab; `/delays/live` has no current frontend caller (all in one router file) |
| `api/routers/reports.py` | `/forecast/heatmap` (and `/forecast/overview`) used by the hourly route drill-down |
| `api/range.py` | `RangeCtx`, `get_range_ctx`, `build_agg_stop_filter` (Postgres agg), `build_updates_filter_ch` (ClickHouse live) |
| `api/clickhouse.py` | `max_captured_at` and the async ClickHouse client wiring |
| `pipeline/analyze.py` | Builds every `agg_*` table the heatmap/summary endpoints read (`agg_stop_daily`, `agg_stop_routes`, `agg_route_stop_daily`, `agg_route_daily`, `agg_route_stats`, `agg_route_hour_dow`, `agg_feed_health`); run via `make analyze` after ingest |

## How to verify manually

**Automated tests:**

- `tests/api/test_api_map.py` — the main suite for `api/routers/map.py`:
  `/delays/live`, `/route-shape`, `/today/route-summary`, trip/stop-profile
  drilldowns. Uses `map_client`/`map_app_ch` fixtures; `map_app_ch` wires a
  real ClickHouse client and needs `RUN_CH_INTEGRATION=1` + `make ch-test`.
- `tests/api/test_map_heatmap.py` — `GET /delays/heatmap`, specifically
  the `p90_delay_min` field, seeded through `agg_stop_daily`.
- `tests/api/test_forecast_heatmap.py` / `tests/unit/test_forecast_heatmap.py`
  — `/forecast/heatmap` (day×hour grid).
- Frontend (no single monolithic `MapTab.test.tsx` exists — coverage is
  per-subcomponent):
  `frontend/src/tabs/map/useHeatmapLayer.test.ts`,
  `useRouteOverlay.test.ts`, `useBasemapDim.test.ts`,
  `routeTrendSegments.test.ts`, `styleReady.test.ts`,
  `expectedDelay.test.ts`, `MapHourScrubber.test.tsx`,
  `MapStyleControl.test.tsx`, `RouteModeToggle.test.tsx`;
  `frontend/src/components/MapLegend.test.tsx`,
  `MapPopupHTML.test.ts`; `frontend/src/styles/mapStyle.test.ts`.
  `frontend/src/test/mockMap.ts` is the shared MapLibre mock these use.
  Note: `CLAUDE.md`'s example `npm run test -- MapTab` filter matches no
  file today (there's no `MapTab.test.*`); use `npm run test -- map` or
  target one of the files above.

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make serve` (FastAPI `:8000`) + `make frontend-dev` (Vite `:5173`),
   or single-origin `make serve` alone after `make bootstrap`.
2. Pick/land on an agency, then click **Map** in the sidebar → URL
   `/agencies/:agencyId/map`.
3. Data must exist first: `make fetch-ingest` (or `ingest_live` +
   `make load_static`), then `make analyze` for the agency — otherwise
   the heatmap shows `map.empty.title`/`hint` until `agg_stop_daily` has
   rows.
4. Interactions to click through:
   - Basemap: click the "Layers" pill bottom-left, switch among
     OSM/淡色/標準/航空写真 — verify the heatmap dots/clusters and legend
     survive the switch (exercises the `styleEpoch` re-attach path in
     `useBasemapDim`/`useHeatmapLayer`/`useRouteOverlay`).
   - Legend: click a severity swatch — map should filter to only that
     band; toggle "show single-sample stops".
   - Heatmap metric toggle — switch avg ↔ p90 coloring.
   - Filters: open the filter bar, pick a `dow`/`service`/`time_band`,
     apply — heatmap should refetch and re-cluster.
   - Route filter: pick exactly one route — heatmap dots hide and a
     single polyline + numbered stops draw (`useRouteOverlay`); the
     `RouteModeToggle` and (in "hourly" mode) `MapHourScrubber` appear;
     press play on the scrubber and watch the line recolor hourly.
   - Click a stop dot or cluster bubble — cluster zooms in; a stop opens
     a popup with name/avg delay/samples/contributing routes.
5. Watch the network tab: a default (no route/time_band) load issues
   `GET /api/{id}/delays/heatmap` with no narrowing params; selecting a
   single route adds both a `GET /api/{id}/route-shape?...` call and a
   `GET /api/{id}/forecast/heatmap?route=...` call (the latter fires in
   trend mode too, not just hourly). The heatmap call should stay
   fast/aggregate-backed regardless of `time_band`, while `route-shape` is
   the one that's visibly a live ClickHouse round trip.

## i18n

- Locale files: `frontend/src/i18n/locales/{en,ja}.json`, namespace
  `"map"`, with sub-namespaces `legend`, `popup`, `hint`, `empty`,
  `style`, `heatmapMode`, `scrubber`, `route_mode` — key parity confirmed
  present in both locales.
- Also relevant: `nav.map` / `nav.map_subtitle` (sidebar label), and the
  shared `filters.*` namespace (`filters.dow.*`, `filters.service.*`,
  `filters.time_band.*`, `filters.routes.*`) used by `TabFilterBar`.
- Per `CLAUDE.md`: every user-visible string must go through `t()` with
  keys in both locale files (CI-linted via `npm run lint:i18n`), and
  hardcoded kana in `.ts/.tsx` fails `npm run lint:i18n-strings` unless
  marked `i18n-ignore` (used deliberately in `TabFilterBar.tsx` for the
  raw 平日/土日祝 query-contract values, and in `mapStyle.ts` for the
  legally-required GSI attribution string "© 国土地理院").
