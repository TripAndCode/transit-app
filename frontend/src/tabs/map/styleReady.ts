import type { Map as MLMap } from "maplibre-gl";

/**
 * Run `fn` once the map's style AND its sources are fully loaded, reliably
 * across `setStyle()` reloads (basemap switch, UI-language change).
 *
 * `isStyleLoaded()` is false not only while a style loads but while its basemap
 * source tiles are still fetching. So right after a `setStyle()`'s one-shot
 * `style.load` fires, an overlay re-run can observe `isStyleLoaded() === false`
 * and — with the old `once("style.load", fn)` — wait for an event that already
 * fired and never comes again, leaving the overlay silently un-attached. Whether
 * it broke depended on tile speed (flaky: cached → fine, cold → blank).
 *
 * We trigger on two events: `styledata` (fast — fires as the style/sources
 * settle, gated on `isStyleLoaded()`) and `idle` (reliable backstop — fires once
 * the map finishes all pending work). The backstop matters because raster tile
 * loads emit `sourcedata`/`data`, not always `styledata`, so the
 * `isStyleLoaded()` transition can fall between `styledata` events and never be
 * caught. `idle` guarantees `fn` runs once the switch fully settles.
 *
 * Returns a cleanup that detaches both listeners if the caller's effect re-runs
 * before the style settles.
 */
export function whenStyleReady(map: MLMap, fn: () => void): () => void {
  if (map.isStyleLoaded()) {
    fn();
    return () => {};
  }
  let done = false;
  const detach = () => {
    map.off("styledata", onData);
    map.off("idle", finish);
  };
  const finish = () => {
    if (done) return;
    done = true;
    detach();
    fn();
  };
  // Fast path: apply as soon as a styledata event reports the style fully ready.
  const onData = () => {
    if (map.isStyleLoaded()) finish();
  };
  map.on("styledata", onData);
  map.on("idle", finish);
  return detach;
}
