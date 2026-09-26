import { describe, it, expect, vi, afterEach } from "vitest";
import { act } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { AnalysisMap } from "./AnalysisMap";
import { accentColorResolved, severeColorResolved, surfaceColorResolved } from "../../styles/tokens";
import { applyTheme } from "../../styles/theme";
import type { RouteShapeResponse } from "../../api/types";

/** Records the layer/source/paint mutations AnalysisMap makes, without WebGL.
 *  Hoisted so the `vi.mock` factory (itself hoisted above the imports) can
 *  close over it. */
const recorder = vi.hoisted(() => ({
  layers: [] as Array<{ id: string; paint?: Record<string, unknown> }>,
  sources: {} as Record<string, unknown>,
  paint: {} as Record<string, unknown>,
  reset() {
    recorder.layers = [];
    recorder.sources = {};
    recorder.paint = {};
  },
}));

vi.mock("maplibre-gl", () => {
  class MockMap {
    constructor(public options: Record<string, unknown>) {}
    addControl() {}
    on() {}
    off() {}
    once() {}
    resize() {}
    remove() {}
    setStyle() {}
    fitBounds() {}
    isStyleLoaded() {
      return true;
    }
    getLayer(id: string) {
      return recorder.layers.find((l) => l.id === id);
    }
    getSource(id: string) {
      return recorder.sources[id];
    }
    addSource(id: string, def: Record<string, unknown>) {
      recorder.sources[id] = { ...def, setData() {} };
    }
    addLayer(layer: { id: string; paint?: Record<string, unknown> }) {
      recorder.layers.push(layer);
    }
    setPaintProperty(layerId: string, prop: string, value: unknown) {
      recorder.paint[`${layerId}|${prop}`] = value;
    }
    getPaintProperty(layerId: string, prop: string) {
      return recorder.paint[`${layerId}|${prop}`];
    }
  }
  class MockNavigationControl {}
  class MockLngLatBounds {
    extend() {
      return this;
    }
  }
  const maplibregl = { Map: MockMap, NavigationControl: MockNavigationControl, LngLatBounds: MockLngLatBounds };
  return {
    default: maplibregl,
    Map: MockMap,
    NavigationControl: MockNavigationControl,
    LngLatBounds: MockLngLatBounds,
  };
});

const LIGHT_ACCENT = "#187b80";
const DARK_ACCENT = "#4fd1c5";

function shape(): RouteShapeResponse {
  return {
    route: "R1",
    geometry: { type: "LineString", coordinates: [[140.7, 40.8], [140.75, 40.83]] },
    stops: [
      { stop_sequence: 1, stop_name: "A", lon: 140.7, lat: 40.8, avg_min: 1.2, samples: 4 },
      { stop_sequence: 2, stop_name: "B", lon: 140.75, lat: 40.83, avg_min: 6.5, samples: 4 },
    ],
  };
}

function layerPaint(id: string): Record<string, unknown> {
  const layer = recorder.layers.find((l) => l.id === id);
  expect(layer, `layer ${id} was never added`).toBeDefined();
  return layer!.paint ?? {};
}

describe("AnalysisMap", () => {
  afterEach(() => {
    recorder.reset();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.removeProperty("--accent");
  });

  it("mounts where ResizeObserver is unavailable", () => {
    // The gap MapTab guards. Forced rather than inherited: the shared setup
    // installs an inert ResizeObserver so components that observe without
    // checking still mount, so absence is no longer the ambient default.
    vi.stubGlobal("ResizeObserver", undefined);
    expect(globalThis.ResizeObserver).toBeUndefined();
    expect(() => renderWithProviders(<AnalysisMap data={shape()} selected={undefined} />)).not.toThrow();
  });

  it("paints the route line and selected stop from the resolved theme tokens", () => {
    renderWithProviders(<AnalysisMap data={shape()} selected={shape().stops[1]} />);
    expect(layerPaint("analysis-line")["line-color"]).toBe(accentColorResolved());
    expect(layerPaint("analysis-stop")["circle-color"]).toBe(severeColorResolved());
    expect(layerPaint("analysis-stop")["circle-stroke-color"]).toBe(surfaceColorResolved());
    for (const value of [
      layerPaint("analysis-line")["line-color"],
      layerPaint("analysis-stop")["circle-color"],
      layerPaint("analysis-stop")["circle-stroke-color"],
    ]) {
      // MapLibre paint expressions cannot consume a CSS var().
      expect(String(value)).not.toContain("var(");
    }
  });

  it("recolours the existing layers when the theme toggles", () => {
    document.documentElement.dataset.theme = "light";
    document.documentElement.style.setProperty("--accent", LIGHT_ACCENT);
    renderWithProviders(<AnalysisMap data={shape()} selected={shape().stops[1]} />);
    expect(layerPaint("analysis-line")["line-color"]).toBe(LIGHT_ACCENT);

    // The source already exists, so the re-run takes the update branch rather
    // than re-adding the layers — the path where a resolved colour can be left
    // behind at the theme that created it.
    document.documentElement.style.setProperty("--accent", DARK_ACCENT);
    act(() => applyTheme("dark"));

    expect(recorder.paint["analysis-line|line-color"]).toBe(DARK_ACCENT);
    expect(recorder.paint["analysis-stop|circle-color"]).toBe(severeColorResolved());
    expect(recorder.paint["analysis-stop|circle-stroke-color"]).toBe(surfaceColorResolved());
  });
});
