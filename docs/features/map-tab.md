# Operations map

The Operations map is a current-service triage workspace for analysts and
transportation operators. It answers four questions on one screen:

1. Which direction is the selected trip running?
2. At which stop did each trip most recently report a delay?
3. At which reported stops did that delay grow or recover?
4. Is the realtime feed fresh enough to trust?

It deliberately does not provide a second historical-map mode. Historical
questions belong in Analysis, linked from the mode switch in the page header.

## Location semantics

The map does not require or imply GPS vehicle positions. Oracle collectors can
capture GTFS-Realtime `TripUpdate` messages every 30 to 60 seconds. For each
trip, the API selects the nearest stop update from its newest poll and locates
that report using the static GTFS stop coordinates.

Resolution order:

1. Match the realtime `stop_id` to `static_stops`.
2. If the feed omits `stop_id`, resolve it through
   `(trip_id, stop_sequence)` in `static_stop_times`.
3. Read `stop_lat` and `stop_lon` from `static_stops`.

For a selected trip, `/delays/live-progress` applies the same nearest-stop rule
to each source snapshot from the preceding six hours and keeps the newest
report for each stop sequence. Its trail therefore means "reported progression"
rather than a confirmed stop crossing.

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
- With all routes selected, the right panel lists routes from the latest
  observation by maximum delay instead of leaving the panel empty.
- Selecting a route groups simultaneous trips by GTFS `direction_id`, falling
  back to `trip_headsign` when older static data has no direction field.
- Selecting a trip emphasizes its latest report and reported stop trail.
- Selecting **All routes** removes the route line and retains all trip markers.
- Trips at the same map position cluster into a count marker until zoomed in.
- Clicking a marker shows route, delay, reported stop, and update age.
- The right panel shows stop-by-stop delay values, a trend chart, and the
  largest delay growth/recovery insight for the selected trip.
- The client refetches stored current reports and selected-trip progress every
  30 seconds. Manual refresh performs the same reads immediately; it does not
  directly trigger the Oracle collector or upstream provider.

## Data path

| Frontend hook | Endpoint | Purpose |
|---|---|---|
| `useLiveTrips` | `GET /api/{agency_id}/delays/live` | One latest report per trip from the feed's rolling five-minute window, enriched with static stop coordinates and headsign |
| `useLiveTripProgress` | `GET /api/{agency_id}/delays/live-progress?trip_id=...` | Nearest reported stop per source snapshot for one trip, compacted to one report per sequence |
| `useTodayRouteSummary` | `GET /api/{agency_id}/today/route-summary` | Historical route average and p90 baseline used to classify current route delay as normal, watch, or anomaly |
| `useRouteShape` | `GET /api/{agency_id}/route-shape` | Static GTFS geometry for the selected route |

`/delays/live` deduplicates the newest five-minute feed window relative to the
agency's latest stored observation to one row per trip and returns at most 500
rows. This remains useful for replayed or delayed feeds, so the UI always shows
the observation age and calls the count "trips in latest observation" rather
than claiming stale rows are physically operating now.

## Key files

| File | Role |
|---|---|
| `frontend/src/tabs/MapTab.tsx` | Operations workspace, MapLibre lifecycle, marker interactions, freshness, and route selection |
| `frontend/src/tabs/map/useOperationsMapLayers.ts` | Clustered trip markers, selected-route shape, and selected-trip report trail |
| `frontend/src/tabs/map/currentRouteStatus.ts` | Current route aggregation and baseline classification |
| `frontend/src/tabs/map/OperationsTripPanel.tsx` | Direction picker, concurrent trips, stop timeline, and delay trend |
| `frontend/src/tabs/map/operationsMap.css` | Desktop and mobile workspace layout |
| `frontend/src/api/hooks.ts` | Current report, route baseline, shape, and detail queries |
| `api/routers/map.py` | Current report enrichment and map/detail endpoints |

MapLibre remains lazy-loaded through `MapTab` so it does not enter the main
application chunk. Basemap switching uses `MapStyleControl`; overlay hooks must
continue using `whenStyleReady()` so sources and layers are restored after a
style change.

## Verification

Automated coverage:

- `tests/api/test_api_map.py` verifies live-trip deduplication, report-history
  compaction, schedule-time normalization, and static stop enrichment.
- `frontend/src/tabs/map/useOperationsMapLayers.test.ts` verifies marker
  filtering, delay labels, route coloring, and overlay removal.
- `frontend/src/tabs/map/currentRouteStatus.test.ts` verifies baseline and
  no-baseline classification, including service-type matching.
- `frontend/src/tabs/map/OperationsTripPanel.test.tsx` verifies direction,
  concurrent-trip selection, and stop progression.
- `frontend/src/routes/legacyRedirects.test.tsx` verifies the `/live` redirect.

Manual checks:

1. Open Operations for an agency with a recent TripUpdate feed.
2. Confirm the freshness timestamp advances after collection and refresh.
3. Compare marker stop names with the latest TripUpdate and static GTFS stop
   coordinates; do not compare them as GPS positions.
4. Select a route, direction, and trip; verify the report trail and chart.
5. Select All routes and verify the route shape disappears.
6. Switch basemaps and confirm markers and the route overlay reappear.
7. Narrow the viewport to verify the trip panel stacks below the map on mobile.

## Related historical map code

The aggregate heatmap and hourly route visualization modules remain available
for historical analysis work, but they are no longer composed by `MapTab`.
New historical-map features should start from a clear analyst question and live
under Analysis rather than add a second mode to Operations.
