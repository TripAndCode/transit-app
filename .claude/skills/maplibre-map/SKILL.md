---
name: maplibre-map
description: MapLibre GL map conventions for this repo (GSI basemaps, style switching, layer re-attach). Use when editing the Map tab, basemap switcher, heatmap, or POI layers.
---

# MapLibre map — transit-app

This repo uses MapLibre GL JS with free GSI basemaps (淡色/標準/航空写真) — NOT
Mapbox. Ignore Mapbox style-spec / token / Studio guidance.

## Conventions
- Keep MapLibre out of the entry chunk: map routes/pages are `React.lazy`.
- React 19 compiler is on — no `useMemo`/`useCallback`/`React.memo`. For
  fresh-props-in-stable-handlers use `useEffectEvent` (see `MapTab`).

## Known gotchas
- `setStyle` drops custom layers/sources. Re-attach them after a style switch —
  this repo tracks a `styleEpoch` and re-adds on change.
- Adding layers before the style is ready silently no-ops. Do NOT rely on
  `isStyleLoaded()` or a one-shot `once('style.load')` to gate the re-attach —
  `isStyleLoaded()` stays false while raster tiles load, so the one-shot is missed
  and the overlay vanishes (flaky by tile speed). Use the repo's
  `styleReady.ts` helper (waits on `styledata` + an `idle` backstop).
- POI names exist in dual casing — handle both when matching/labelling.

## Data viz
- Encode signal, not volume: marker/heat size should track the metric (delay),
  not sample count.
- Heatmap is served entirely from precomputed aggregates (no live `updates`
  scan): no route filter → `agg_stop_daily`; a route filter → `agg_route_stop_daily`.
  Both are deduped to one row per trip-stop event, so `samples` = observations, not
  feed polls. Re-run `analyze` after ingest or the heatmap goes empty/stale.
