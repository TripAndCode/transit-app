import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";

// MapLibre looks for its worker beside its own module URL. Bundling moves that
// module into a chunk with no worker next to it, so the worker is emitted as
// an asset of its own and its URL handed over before any map exists.
maplibregl.setWorkerUrl(workerUrl);

/**
 * Construct a MapLibre map, catching the synchronous throw some browsers/test
 * environments raise when WebGL is unavailable. On failure, `onUnavailable` is
 * scheduled via `requestAnimationFrame` (deferring the resulting state update
 * out of the current effect pass) and the returned `cleanup` cancels that
 * frame if the caller unmounts before it fires.
 */
export function createSafeMap(
  options: maplibregl.MapOptions,
  onUnavailable: () => void,
): { map: maplibregl.Map; cleanup?: undefined } | { map?: undefined; cleanup: () => void } {
  try {
    return { map: new maplibregl.Map(options) };
  } catch {
    const frame = requestAnimationFrame(onUnavailable);
    return { cleanup: () => cancelAnimationFrame(frame) };
  }
}
