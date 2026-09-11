# Operations map

The Operations map is a current-service triage workspace for analysts and
transportation operators. It answers three questions on one screen:

1. Which routes need attention now?
2. At which stop did each active trip most recently report a delay?
3. Is the realtime feed fresh enough to trust?

It deliberately does not provide a second historical-map mode. Historical
questions belong in Analysis, linked from the mode switch in the page header.

## Location semantics

The map does not require or imply GPS vehicle positions. Oracle collectors
ingest GTFS-Realtime `TripUpdate` messages every 30 to 60 seconds. For each
trip, the API selects the nearest stop update from its newest poll and locates
that report using the static GTFS stop coordinates.

Resolution order:

1. Match the realtime `stop_id` to `static_stops`.
2. If the feed omits `stop_id`, resolve it through
   `(trip_id, stop_sequence)` in `static_stop_times`.
3. Read `stop_lat` and `stop_lon` from `static_stops`.

The marker therefore means "this trip's latest reported stop and delay," not
"the vehicle is physically at this coordinate." The UI states this distinction
below the map. Trips whose stops cannot be resolved remain in summary counts
but are not plotted.

## User flow

- Sidebar item **Operations** opens `/agencies/:agencyId/map`.
- `/agencies/:agencyId/live` redirects to the same workspace and preserves the
  agency and query string.
- **Current** is the active mode. **Historical analysis** links to
  `/agencies/:agencyId/analysis/trend`.
- Selecting a route emphasizes its latest trip reports and static GTFS shape.
- Selecting **All routes** removes the route line and retains all trip markers.
- Clicking a marker shows route, delay, reported stop, and update age.
- The priority queue orders anomaly and watch routes and links to the existing
  trip drill-down.
- The client refetches current reports every 30 seconds and also offers manual
  refresh.

## Data path

| Frontend hook | Endpoint | Purpose |
|---|---|---|
| `useLiveTrips` | `GET /api/{agency_id}/delays/live` | One latest report per trip from the feed's rolling five-minute window, enriched with static stop coordinates and headsign |
| `useTodayRouteSummary` | `GET /api/{agency_id}/today/route-summary` | Historical route average and p90 baseline used to classify current route delay as normal, watch, or anomaly |
| `useRouteShape` | `GET /api/{agency_id}/route-shape` | Static GTFS geometry for the selected route |
| `useRouteTrips` / `useRouteStopProfile` | `GET /api/{agency_id}/today/route/{route}/...` | Existing details shown from the priority queue |

`/delays/live` deduplicates the newest five-minute feed window to one row per
trip and returns at most 500 rows. Current severity uses the active trips'
average delay. When a matching service-type baseline exists, anomaly means
above its p90 and watch means above the midpoint between average and p90. A
route without a baseline still appears: at least five minutes is anomaly and
at least three minutes is watch.

## Key files

| File | Role |
|---|---|
| `frontend/src/tabs/MapTab.tsx` | Operations workspace, MapLibre lifecycle, marker interactions, freshness, and route selection |
| `frontend/src/tabs/map/useOperationsMapLayers.ts` | Current-trip marker and selected-route GeoJSON layers |
| `frontend/src/tabs/map/currentRouteStatus.ts` | Current route aggregation and baseline classification |
| `frontend/src/tabs/map/OperationsQueue.tsx` | Priority queue and route actions |
| `frontend/src/tabs/map/operationsMap.css` | Desktop and mobile workspace layout |
| `frontend/src/tabs/live/RouteDrilldown.tsx` | Existing trip detail reused by Operations |
| `frontend/src/api/hooks.ts` | Current report, route baseline, shape, and detail queries |
| `api/routers/map.py` | Current report enrichment and map/detail endpoints |

MapLibre remains lazy-loaded through `MapTab` so it does not enter the main
application chunk. Basemap switching uses `MapStyleControl`; overlay hooks must
continue using `whenStyleReady()` so sources and layers are restored after a
style change.

## Verification

Automated coverage:

- `tests/api/test_api_map.py` verifies live-trip deduplication, schedule-time
  normalization, and stop-coordinate fallback through `static_stop_times`.
- `frontend/src/tabs/map/useOperationsMapLayers.test.ts` verifies marker
  filtering, delay labels, route coloring, and overlay removal.
- `frontend/src/tabs/map/currentRouteStatus.test.ts` verifies baseline and
  no-baseline classification, including service-type matching.
- `frontend/src/tabs/map/OperationsQueue.test.tsx` verifies priority rendering
  and route actions.
- `frontend/src/routes/legacyRedirects.test.tsx` verifies the `/live` redirect.

Manual checks:

1. Open Operations for an agency with a recent TripUpdate feed.
2. Confirm the freshness timestamp advances after collection and refresh.
3. Compare marker stop names with the latest TripUpdate and static GTFS stop
   coordinates; do not compare them as GPS positions.
4. Select a route and verify its shape and markers are emphasized.
5. Select All routes and verify the route shape disappears.
6. Switch basemaps and confirm markers and the route overlay reappear.
7. Open a priority route and confirm its trip drill-down loads.
8. Narrow the viewport to verify the queue stacks below the map on mobile.

## Related historical map code

The aggregate heatmap and hourly route visualization modules remain available
for historical analysis work, but they are no longer composed by `MapTab`.
New historical-map features should start from a clear analyst question and live
under Analysis rather than add a second mode to Operations.
