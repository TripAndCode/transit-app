// Minimal MapLibre stand-in for overlay-hook tests: records the layer/source/paint
// mutations the hooks make, without any WebGL. Layers are an ordered array so tests
// can assert stacking order (basemap -> scrim -> overlay).
export interface MockLayer {
  id: string;
  type?: string;
  source?: string;
  paint?: Record<string, unknown>;
  [k: string]: unknown;
}

export function makeMockMap(
  initialLayers: MockLayer[] = [{ id: "basemap", type: "raster" }],
  styleLoaded = true,
) {
  const layers: MockLayer[] = [...initialLayers];
  const sources: Record<string, unknown> = {};
  const paint: Record<string, unknown> = {};
  let styleLoadedFlag = styleLoaded;
  const map = {
    layers,
    sources,
    paint,
    getLayer: (id: string) => layers.find((l) => l.id === id),
    removeLayer: (id: string) => {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
    },
    getSource: (id: string) => sources[id],
    addSource: (id: string, def: Record<string, unknown>) => {
      // mirror maplibre: getSource(id) returns an object with setData()
      sources[id] = { ...def, setData: (d: unknown) => { (sources[id] as Record<string, unknown>).data = d; } };
    },
    addLayer: (layer: MockLayer, beforeId?: string) => {
      if (beforeId) {
        const i = layers.findIndex((l) => l.id === beforeId);
        layers.splice(i < 0 ? layers.length : i, 0, layer);
      } else {
        layers.push(layer);
      }
    },
    setPaintProperty: (layerId: string, prop: string, value: unknown) => {
      paint[`${layerId}|${prop}`] = value;
    },
    getPaintProperty: (layerId: string, prop: string) => paint[`${layerId}|${prop}`],
    getStyle: () => ({ layers }),
    isStyleLoaded: () => styleLoadedFlag,
    // No-op recorder: the code under test calls map.once("style.load", …);
    // readiness is driven via settleStyle/settleViaIdle (styledata + idle
    // backstop), so the one-shot never needs to fire in tests.
    once: () => {},
    _handlers: {} as Record<string, Array<() => void>>,
    on: (event: string, cb: () => void) => {
      (map._handlers[event] ||= []).push(cb);
    },
    off: (event: string, cb: () => void) => {
      const a = map._handlers[event];
      if (a) {
        const i = a.indexOf(cb);
        if (i >= 0) a.splice(i, 1);
      }
    },
    fire: (event: string) => {
      (map._handlers[event] || []).slice().forEach((cb) => cb());
    },
    // Simulate the style + its sources finishing via a qualifying `styledata`
    // (the fast path): flips isStyleLoaded() true and emits styledata.
    settleStyle: () => {
      styleLoadedFlag = true;
      map.fire("styledata");
    },
    // Simulate the real raster-tile case: the style becomes loaded but the
    // transition is only signalled by `idle` (no further `styledata`), which is
    // the backstop whenStyleReady must rely on.
    settleViaIdle: () => {
      styleLoadedFlag = true;
      map.fire("idle");
    },
    flyTo: () => {},
    fitBounds: () => {},
  };
  return map;
}
export type MockMap = ReturnType<typeof makeMockMap>;
