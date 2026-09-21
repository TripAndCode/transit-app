// Stand-in for the `maplibre-gl` package in tests: jsdom has no WebGL, so a
// real `new maplibregl.Map(...)` throws synchronously (handled in product
// code by createSafeMap's try/catch). Rather than depend on that throw path
// (whose exact behavior belongs to the real library, not this repo), tests
// that need a working map replace the whole module with this deterministic
// fake via `vi.mock("maplibre-gl", () => import("./maplibreMock"))`.
export class MockMap {
  options: Record<string, unknown>;
  private handlers: Record<string, Array<(...args: unknown[]) => void>> = {};

  constructor(options: Record<string, unknown>) {
    this.options = options;
  }

  addControl() {}

  on(event: string, ...args: unknown[]) {
    const cb = args[args.length - 1] as (...a: unknown[]) => void;
    (this.handlers[event] ??= []).push(cb);
  }

  off(event: string, ...args: unknown[]) {
    const cb = args[args.length - 1] as (...a: unknown[]) => void;
    const list = this.handlers[event];
    if (!list) return;
    const i = list.indexOf(cb);
    if (i >= 0) list.splice(i, 1);
  }

  once() {
    // The real map's one-shot "style.load" callback is never needed in these
    // smoke tests (no style switch is triggered), so it's a no-op here.
  }

  fire(event: string) {
    (this.handlers[event] ?? []).slice().forEach((cb) => cb());
  }

  getCanvas() {
    return { style: {} } as unknown as HTMLCanvasElement;
  }

  resize() {}
  remove() {}
  setStyle() {}
  fitBounds() {}
  easeTo() {}
  getZoom() {
    return 11;
  }
  isStyleLoaded() {
    return true;
  }
  getStyle() {
    return { layers: [] };
  }
  getLayer() {
    return undefined;
  }
  getSource() {
    return undefined;
  }
  addSource() {}
  addLayer() {}
  removeLayer() {}
  removeSource() {}
  setPaintProperty() {}
  getPaintProperty() {
    return undefined;
  }
}

export class MockPopup {
  setLngLat() {
    return this;
  }
  setDOMContent() {
    return this;
  }
  addTo() {
    return this;
  }
  remove() {
    return this;
  }
}

export class MockNavigationControl {}

export class MockLngLatBounds {
  extend() {
    return this;
  }
}

const maplibregl = {
  Map: MockMap,
  Popup: MockPopup,
  NavigationControl: MockNavigationControl,
  LngLatBounds: MockLngLatBounds,
};

export default maplibregl;
export const Map = MockMap;
export const Popup = MockPopup;
export const NavigationControl = MockNavigationControl;
export const LngLatBounds = MockLngLatBounds;
