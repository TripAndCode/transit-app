# Operations map

The Operations map is a current-service triage workspace for analysts and
transportation operators. It answers four questions on one screen:

1. Which direction is the selected trip running?
2. At which stop did each trip most recently report a delay?
3. At which reported stops did that delay grow or recover?
4. Is the realtime feed fresh enough to trust?

A day-playback mode (below) replays how delay moved across the whole service
day on this same map, so a look back at "how did today unfold" never needs a
second map screen. Deeper historical analysis — trend lines, route
comparisons, forecasts — stays in Analysis; a delayed trip's row in the
attention panel links straight to that route's Route analysis view.

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

- The sidebar's first entry (`SIDEBAR_NAV_ITEMS` in
  `frontend/src/components/sidebarNavItems.ts`, labeled from the
  `design:overview` i18n key) opens `/agencies/:agencyId/operations`, the
  single mount point for this tab. A bare `/agencies/:agencyId` lands here
  too.
- `/agencies/:agencyId/overview`, `/agencies/:agencyId/map` and
  `/agencies/:agencyId/live` all redirect here, preserving the agency and
  query string. They render a redirect only — this component is mounted once,
  so navigating between those URLs never tears down and rebuilds MapLibre's GL
  context.
- The period-summary view at `/agencies/:agencyId/period-overview` is a
  different tab — see `docs/features/overview-tab.md`.
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
- The client refetches current reports and selected-trip progress every 30
  seconds. Manual refresh first pulls the Oracle collector's newest loose
  protobuf into ClickHouse when the local-dev SSH transport is configured;
  environments without that transport use the agency's live feed URL as a
  fallback, then the client reads the newly persisted data.
- A delayed trip's row in the attention panel links to
  `/agencies/:agencyId/route-analysis?routes=<route_code>`, the one deep link
  from this tab into a historical view.

## Day playback

`FilterDock`'s "Play the day" toggle switches the map from current
observations to a replay of one whole service day, one hour at a time
(`operations.playback.toggle_on`/`toggle_off`, `Clapperboard` icon).

- `useDayPlayback` fetches `GET /api/{agency_id}/delays/timeline` once
  playback is switched on and derives the playhead from elapsed wall-clock
  time rather than incrementing per tick, so a delayed timer tick lands on the
  frame the clock says it is instead of drifting behind.
- With no `date` given, the endpoint resolves to the agency's latest observed
  JST day. Frames cover the 05:00–24:00 service window in 60-minute buckets
  (the endpoint also accepts a 15-minute step; the UI does not yet expose
  choosing it); each bucket needs at least 3 observations at a stop before
  that stop is drawn, and a frame is capped at 400 points.
- `useTimelineLayers` hides the live trip layers for as long as playback is on
  and instead draws the current frame's stops plus its two preceding frames as
  a fading trail (`GHOST_OPACITY`), colored by delay severity the same way the
  live markers are.
- `PlaybackRail`, pinned to the bottom of the map, provides play/pause, a
  scrubber whose track is pre-colored by each frame's own mean delay, a
  1x/2x speed toggle, the current frame's clock and date, and an exit
  control. Playback loops at the end of the day instead of stopping.
- A viewer with `prefers-reduced-motion` gets step-forward/step-back buttons
  instead of autoplay; the rail also answers arrow-key and Space input for
  stepping/toggling.

## Data path

| Frontend hook | Endpoint | Purpose |
|---|---|---|
| `useLiveTrips` | `GET /api/{agency_id}/delays/live` | One latest report per trip from the feed's rolling five-minute window, enriched with static stop coordinates and headsign |
| `useLiveTripProgress` | `GET /api/{agency_id}/delays/live-progress?trip_id=...` | Nearest reported stop per source snapshot for one trip, compacted to one report per sequence |
| `useTodayRouteSummary` | `GET /api/{agency_id}/today/route-summary` | Historical route average and p90 baseline used to classify current route delay as normal, watch, or anomaly |
| `useRouteShape` | `GET /api/{agency_id}/route-shape` | Static GTFS geometry for the selected route |
| `useTimeline` | `GET /api/{agency_id}/delays/timeline` | One service day's worth of per-stop delay frames, backing day playback |

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
| `frontend/src/tabs/map/FilterDock.tsx` | Route/pattern filter control and the day-playback toggle |
| `frontend/src/tabs/map/useDayPlayback.ts` | Playback clock/state: frame index, play/pause, speed |
| `frontend/src/tabs/map/playbackFrames.ts` | Pure frame-index math and the timeline GeoJSON builder |
| `frontend/src/tabs/map/PlaybackRail.tsx` | Playback transport UI pinned to the bottom of the map |
| `frontend/src/tabs/map/useTimelineLayers.ts` | Playback map layer; hides/restores the live trip layers |
| `frontend/src/api/hooks.ts` | Current report, route baseline, shape, timeline, and detail queries |
| `api/routers/map.py` | Current report enrichment and map/detail/timeline endpoints |
| `pipeline/reports/timeline.py` | `compute_delay_timeline()` — per-bucket positioned stop delays for one service day |

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
- `frontend/src/tabs/map/playbackFrames.test.ts` verifies frame-index math
  (advance, wrap, clamp) and the ghost-trail GeoJSON builder.
- `frontend/src/tabs/map/useDayPlayback.test.ts` verifies the playback clock,
  play/pause/step, speed changes, and index clamping when a shorter day loads.
- `frontend/src/tabs/map/PlaybackRail.test.tsx` verifies the transport UI,
  keyboard control, and the reduced-motion stepping fallback.
- `frontend/src/tabs/map/useTimelineLayers.test.ts` verifies the playback
  layer is a no-op while playback is off and restores the live layers on exit.

Manual checks:

1. Open Operations for an agency with a recent TripUpdate feed.
2. Confirm the freshness timestamp advances after collection and refresh.
3. Compare marker stop names with the latest TripUpdate and static GTFS stop
   coordinates; do not compare them as GPS positions.
4. Select a route, direction, and trip; verify the report trail and chart.
5. Select All routes and verify the route shape disappears.
6. Switch basemaps and confirm markers and the route overlay reappear.
7. Narrow the viewport to verify the trip panel stacks below the map on mobile.
8. Toggle "Play the day" and confirm the live layers disappear, the rail
   appears with a pre-colored scrubber, and pressing play advances the clock
   and the map's stop trail together; confirm it loops past the last frame
   instead of stopping.
9. With reduced motion enabled at the OS level, confirm the rail offers only
   step buttons and never autoplays.
