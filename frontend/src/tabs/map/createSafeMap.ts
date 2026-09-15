import maplibregl from "maplibre-gl";

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
