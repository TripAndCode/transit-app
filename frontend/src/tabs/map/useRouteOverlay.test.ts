import { describe, it, expect, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap, type MockLayer } from "../../test/mockMap";
import { useRouteOverlay, ROUTE_STOPS_LAYER, ROUTE_LAYER } from "./useRouteOverlay";
import type { RouteShapeResponse } from "../../api/types";

const SHAPE = {
  geometry: { type: "LineString", coordinates: [[140.7, 40.8], [140.8, 40.9]] },
  stops: [
    { stop_sequence: 1, stop_name: "A", lon: 140.7, lat: 40.8, avg_min: 12, samples: 30 },
    { stop_sequence: 2, stop_name: "B", lon: 140.8, lat: 40.9, avg_min: 1, samples: 30 },
  ],
  unobserved_stops: [],
} as unknown as RouteShapeResponse;

function run(map: MockMap) {
  return renderHook(() => {
    const mapRef = useRef(map as never);
    useRouteOverlay(mapRef, SHAPE, 0);
  });
}

describe("useRouteOverlay theme reactivity", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("draws the route-stop layer with the severe-colored step expression", () => {
    const map = makeMockMap();
    run(map);
    const layer = map.getLayer(ROUTE_STOPS_LAYER) as MockLayer;
    expect(JSON.stringify(layer.paint!["circle-color"])).toContain("#d92121");
  });

  it("rebuilds the overlay with the dark severe color on themechange", () => {
    const map = makeMockMap();
    // Start in light mode: dark is the default, so a toggle TO dark is only
    // observable from a non-dark starting DOM state. useThemeSignal reads
    // data-theme from the DOM (the source of truth applyTheme writes), so the
    // test sets it exactly as applyTheme would, alongside dispatching the event.
    document.documentElement.dataset.theme = "light";
    run(map);
    expect(
      JSON.stringify((map.getLayer(ROUTE_STOPS_LAYER) as MockLayer).paint!["circle-color"]),
    ).toContain("#d92121");

    // Simulate the cascade resolving the dark value, then toggle the theme —
    // data-theme write + event, exactly what applyTheme does.
    act(() => {
      document.documentElement.style.setProperty("--delay-severe", "#F04438");
      document.documentElement.dataset.theme = "dark";
      window.dispatchEvent(new CustomEvent("themechange", { detail: "dark" }));
    });

    expect(
      JSON.stringify((map.getLayer(ROUTE_STOPS_LAYER) as MockLayer).paint!["circle-color"]),
    ).toContain("#F04438");
  });
});

describe("useRouteOverlay scrubbedDelayMin", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("uses the default flat color when scrubbedDelayMin is not passed", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useRouteOverlay(mapRef, SHAPE, 0);
    });
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layer.paint!["line-color"]).toBe("#5b6cad");
  });

  it("resolves scrubbedDelayMin to a delay-ramp color for the route line", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useRouteOverlay(mapRef, SHAPE, 0, 6);
    });
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layer.paint!["line-color"]).toBe("#e07a3a");
  });

  it("falls back to the flat color when scrubbedDelayMin is explicitly null", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useRouteOverlay(mapRef, SHAPE, 0, null);
    });
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layer.paint!["line-color"]).toBe("#5b6cad");
  });

  it("resolves a >=10min scrubbedDelayMin to a real parseable hex, never the literal var() string", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useRouteOverlay(mapRef, SHAPE, 0, 14);
    });
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    const color = layer.paint!["line-color"] as string;
    expect(color).not.toContain("var(");
    expect(color).toBe("#d92121");
  });

  it("updates the line color via setPaintProperty on a scrub tick, without tearing down and rebuilding the overlay", () => {
    const map = makeMockMap();
    const { rerender } = renderHook(
      ({ delay }: { delay: number }) => {
        const mapRef = useRef(map as never);
        useRouteOverlay(mapRef, SHAPE, 0, delay);
      },
      { initialProps: { delay: 6 } },
    );

    const layerBefore = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layerBefore.paint!["line-color"]).toBe("#e07a3a");

    rerender({ delay: 3 });

    // Same layer object identity: a real teardown/rebuild (clearOverlay() +
    // addLayer()) would replace it with a new object. Regression guard for
    // the bug where every scrub tick tore down and re-added both sources and
    // both layers, causing a visible flicker of the whole overlay each
    // second during playback.
    expect(map.getLayer(ROUTE_LAYER)).toBe(layerBefore);
    expect(map.getPaintProperty(ROUTE_LAYER, "line-color")).toBe("#d4b878");
  });
});

describe("useRouteOverlay route-stops circle-radius", () => {
  it("uses exactly one zoom-based interpolate expression (MapLibre rejects two in one paint property)", () => {
    const map = makeMockMap();
    run(map);
    const layer = map.getLayer(ROUTE_STOPS_LAYER) as MockLayer;
    const zoomInterpolateCount = (JSON.stringify(layer.paint!["circle-radius"]).match(/"zoom"/g) ?? []).length;
    expect(zoomInterpolateCount).toBe(1);
  });
});
