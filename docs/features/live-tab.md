# Live tab (最新観測)

Baseline-relative triage list for the most recently analyzed observation
day: every route bucketed into anomaly / watch / normal / no-baseline by how
far today's figures deviate from its historical baseline, with a per-route
trip/stop drilldown on click.

## How a user reaches it

- Route: `/agencies/:agencyId/live`, registered in `frontend/src/main.tsx`
  (`React.lazy`-loaded).
- Sidebar nav link: `frontend/src/components/Sidebar.tsx` (`nav.live` i18n
  key, labeled "最新観測" / "Latest observations").
- Top-level component: `frontend/src/tabs/LiveTab.tsx` — owns sort/filter
  state, the auto-refresh toggle, and which route's drilldown panel is open.

What the user sees/does:

- **Header** — title with the analyzed date, a staleness badge (red once the
  latest observation is over an hour old, via
  `frontend/src/utils/relativeTime.ts`), an auto-refresh checkbox (polls
  every 30s via `useTodayRouteSummary`'s `refetchInterval`), and a manual
  refresh button.
- **Filter/sort row** — a free-text route filter (matches formatted route
  name or raw route code) and 5 sort pills (deviation / worst / avg / trips
  / name); `deviation` is the default and is really "no re-sort" — it keeps
  each bucket in the backend's own `ORDER BY` (worst delay first).
- **Bucketed route list** — `groupByBucket()` (`frontend/src/tabs/live/bucket.ts`)
  splits routes into 4 sections in fixed order; `anomaly`/`watch` render
  expanded by default, `normal`/`no_baseline` render inside a collapsed
  `<details>`. Each row is `frontend/src/tabs/live/RouteRow.tsx`.
- **Route drilldown** — clicking a row opens
  `frontend/src/tabs/live/RouteDrilldown.tsx`, a slide-over panel with two
  views: per-trip delay (worst first) and the per-stop delay profile
  (where along the route delay builds up).

## Request path

| Frontend hook (`frontend/src/api/hooks.ts`) | Endpoint | Data source |
|---|---|---|
| `useTodayRouteSummary(agencyId, { autoRefresh })` | `GET /api/{agency_id}/today/route-summary` (`api/routers/map.py: today_route_summary`) | Reads the precomputed `agg_route_daily` for the latest analyzed date (not a live `updates` scan — "today" means "as of the last `analyze` run"), joined to `agg_route_stats` for each route's historical baseline. A pure classifier, `api.triage.classify_route`, assigns each row a `bucket`/`deviation_sec`/`low_confidence`. The staleness badge's `latest_captured_at` comes from a separate best-effort ClickHouse freshness probe (`ORDER BY captured_at DESC LIMIT 1`, index-served) that degrades to `null` rather than failing the whole response; `raw_samples`/`clamp_count` (feed health) are summed from `agg_feed_health` over the last 7 analyzed days. |
| `useRouteTrips(agencyId, routeCode)` (fires when a route's drilldown is open) | `GET /api/{agency_id}/today/route/{route_code}/trips` (`api/routers/map.py`) | Live ClickHouse scan of `updates`, bounded to the latest analyzed day. |
| `useRouteStopProfile(agencyId, routeCode)` (fires when a route's drilldown is open) | `GET /api/{agency_id}/today/route/{route_code}/stop-profile` (`api/routers/map.py`) | Live ClickHouse scan of `updates`, joined to Postgres `static_stops`/`static_trips` for stop labels/sequence. |

`api/routers/map.py` hosts these endpoints alongside the Map tab's
`/route-shape` and `/delays/heatmap` — see `docs/features/map-tab.md`'s
request-path table for that split; they're listed here because this is the
tab that actually consumes them.

## Key files

**Frontend**

| File | Role |
|---|---|
| `frontend/src/tabs/LiveTab.tsx` | Live tab shell: sort/filter state, auto-refresh toggle, bucket rendering |
| `frontend/src/tabs/live/bucket.ts` | `groupByBucket()` — splits routes into the 4 fixed-order buckets |
| `frontend/src/tabs/live/RouteRow.tsx` | One route's summary row (avg/worst delay, trip count, deviation) |
| `frontend/src/tabs/live/RouteDrilldown.tsx` | Slide-over panel: per-trip + per-stop views for one route |
| `frontend/src/api/useRouteNames.ts` | Route code → display name formatter used throughout the list |
| `frontend/src/utils/relativeTime.ts` | "X minutes ago" formatting for the staleness badge |
| `frontend/src/api/hooks.ts` | `useTodayRouteSummary`, `useRouteTrips`, `useRouteStopProfile` |

**Backend**

| File | Role |
|---|---|
| `api/routers/map.py` | `/today/route-summary`, `/today/route/{route_code}/trips`, `/today/route/{route_code}/stop-profile` |
| `api/triage.py` | `classify_route()` — the pure anomaly/watch/normal/no_baseline classifier |
| `pipeline/analyze.py` | Builds `agg_route_daily`, `agg_route_stats`, `agg_feed_health` — the aggregates this tab reads |

## How to verify manually

**Automated tests:**

- Backend: `tests/unit/test_triage.py` (pure classifier logic, no DB),
  `tests/api/test_api_map.py` (covers `/today/route-summary` and the
  trip/stop-profile drilldowns — uses the `map_client`/`map_app_ch` fixtures;
  `map_app_ch` needs `RUN_CH_INTEGRATION=1` + `make ch-test`),
  `tests/unit/test_range_jst_today.py` (JST "today" date-boundary logic
  shared with the anchor date this endpoint uses).
- Frontend: `frontend/src/tabs/LiveTab.test.tsx`,
  `frontend/src/tabs/live/bucket.test.tsx`.

**Manual click-through** (`make serve` + `make frontend-dev`):

1. `make bootstrap && make serve` (+ `make frontend-dev`). Load data first:
   `make fetch-ingest` (or `ingest_live` + `make load_static`), then
   `make analyze` for the agency — otherwise the list is empty (no
   `agg_route_daily` rows for "today").
2. Click "最新観測" / "Latest observations" in the sidebar → URL
   `/agencies/:agencyId/live`.
3. Expect routes grouped into anomaly/watch (expanded) and normal/no_baseline
   (collapsed `<details>`) sections; toggle a collapsed section open.
4. Type into the filter box — expect the list to narrow by route
   name/code; try each sort pill (worst/avg/trips/name) and confirm the
   within-bucket order changes while bucket membership stays fixed.
5. Click a route row — expect the slide-over drilldown with per-trip and
   per-stop views; close it via the close button or the click-away scrim.
6. Toggle auto-refresh off/on and use the manual refresh button — watch the
   "updated at" timestamp change; leave the tab open past an hour of no new
   data (or fake it by checking `latest_captured_at` handling) to see the
   staleness badge turn red.

## i18n

- Frontend strings live under the `live.*` namespace in
  `frontend/src/i18n/locales/{ja,en}.json` (key parity CI-linted via
  `npm run lint:i18n`), including `live.bucket.*` (bucket headings),
  `live.sort.*` (sort pill labels), `live.hint.*` (the explainer popover),
  and `live.drill.*` (drilldown panel), plus `nav.live` / `nav.live_subtitle`
  for the sidebar entry.
- `formatLocal()` in `LiveTab.tsx` intentionally hardcodes `"ja-JP"` for the
  "updated at" timestamp regardless of `i18n.language` — a known, tracked
  gap (date-locale switching), not an oversight; see the inline comment.
