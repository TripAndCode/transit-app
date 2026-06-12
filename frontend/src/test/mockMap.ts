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
    addSource: (id: string, def: unknown) => {
      sources[id] = def;
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
    _onceHandlers: {} as Record<string, () => void>,
    once: (event: string, cb: () => void) => {
      map._onceHandlers[event] = cb;
    },
    fireOnce: (event: string) => {
      // a real `style.load` means the style is now loaded
      if (event === "style.load") styleLoadedFlag = true;
      const cb = map._onceHandlers[event];
      if (cb) {
        delete map._onceHandlers[event];
        cb();
      }
    },
    flyTo: () => {},
    fitBounds: () => {},
  };
  return map;
}
export type MockMap = ReturnType<typeof makeMockMap>;
