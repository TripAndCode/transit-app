import { describe, it, expect, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap, type MockLayer } from "../../test/mockMap";
import {
  useRouteOverlay,
  ROUTE_STOPS_LAYER,
  ROUTE_LAYER,
  ROUTE_CASING_LAYER,
  ROUTE_TREND_LAYER,
} from "./useRouteOverlay";
import type { RouteShapeResponse } from "../../api/types";

const SHAPE = {
  geometry: { type: "LineString", coordinates: [[140.7, 40.8], [140.8, 40.9]] },
  stops: [
    { stop_sequence: 1, stop_name: "A", lon: 140.7, lat: 40.8, avg_min: 12, samples: 30 },
    { stop_sequence: 2, stop_name: "B", lon: 140.8, lat: 40.9, avg_min: 1, samples: 30 },
  ],
  unobserved_stops: [],
} as unknown as RouteShapeResponse;

function run(map: MockMap, mode: "trend" | "hourly" = "trend", scrubbedDelayMin: number | null = null) {
  return renderHook(() => {
    const mapRef = useRef(map as never);
    useRouteOverlay(mapRef, SHAPE, 0, mode, scrubbedDelayMin);
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
    document.documentElement.dataset.theme = "light";
    run(map);
    expect(
      JSON.stringify((map.getLayer(ROUTE_STOPS_LAYER) as MockLayer).paint!["circle-color"]),
    ).toContain("#d92121");

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

describe("useRouteOverlay hourly mode (the hour-scrubber's flat line)", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("uses the default flat color when scrubbedDelayMin is not passed", () => {
    const map = makeMockMap();
    run(map, "hourly");
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layer.paint!["line-color"]).toBe("#5b6cad");
  });

  it("resolves scrubbedDelayMin to a delay-ramp color for the route line", () => {
    const map = makeMockMap();
    run(map, "hourly", 6);
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layer.paint!["line-color"]).toBe("#e07a3a");
  });

  it("resolves a >=10min scrubbedDelayMin to a real parseable hex, never the literal var() string", () => {
    const map = makeMockMap();
    run(map, "hourly", 14);
    const layer = map.getLayer(ROUTE_LAYER) as MockLayer;
    const color = layer.paint!["line-color"] as string;
    expect(color).not.toContain("var(");
    expect(color).toBe("#d92121");
  });

  it("does not draw the trend-segment layer in hourly mode", () => {
    const map = makeMockMap();
    run(map, "hourly");
    expect(map.getLayer(ROUTE_TREND_LAYER)).toBeUndefined();
  });

  it("updates the line color via setPaintProperty on a scrub tick, without tearing down and rebuilding the overlay", () => {
    const map = makeMockMap();
    const { rerender } = renderHook(
      ({ delay }: { delay: number }) => {
        const mapRef = useRef(map as never);
        useRouteOverlay(mapRef, SHAPE, 0, "hourly", delay);
      },
      { initialProps: { delay: 6 } },
    );

    const layerBefore = map.getLayer(ROUTE_LAYER) as MockLayer;
    expect(layerBefore.paint!["line-color"]).toBe("#e07a3a");

    rerender({ delay: 3 });

    expect(map.getLayer(ROUTE_LAYER)).toBe(layerBefore);
    expect(map.getPaintProperty(ROUTE_LAYER, "line-color")).toBe("#d4b878");
  });
});

describe("useRouteOverlay trend mode (per-segment coloring)", () => {
  it("draws the trend-segment layer, not the flat hourly line", () => {
    const map = makeMockMap();
    run(map, "trend");
    expect(map.getLayer(ROUTE_TREND_LAYER)).toBeDefined();
    expect(map.getLayer(ROUTE_LAYER)).toBeUndefined();
  });

  it("colors trend segments via the severe-color step expression", () => {
    const map = makeMockMap();
    run(map, "trend");
    const layer = map.getLayer(ROUTE_TREND_LAYER) as MockLayer;
    expect(JSON.stringify(layer.paint!["line-color"])).toContain("#d92121");
  });

  it("still draws the white casing layer in trend mode", () => {
    const map = makeMockMap();
    run(map, "trend");
    expect(map.getLayer(ROUTE_CASING_LAYER)).toBeDefined();
  });

  it("does not call setPaintProperty on ROUTE_LAYER in trend mode (no flat line to update)", () => {
    const map = makeMockMap();
    const { rerender } = renderHook(
      ({ delay }: { delay: number | null }) => {
        const mapRef = useRef(map as never);
        useRouteOverlay(mapRef, SHAPE, 0, "trend", delay);
      },
      { initialProps: { delay: null as number | null } },
    );
    rerender({ delay: 6 });
    expect(map.getPaintProperty(ROUTE_LAYER, "line-color")).toBeUndefined();
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
