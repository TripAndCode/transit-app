import { describe, it, expect, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap, type MockLayer } from "../../test/mockMap";
import { useRouteOverlay, ROUTE_STOPS_LAYER } from "./useRouteOverlay";
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
    run(map);
    expect(
      JSON.stringify((map.getLayer(ROUTE_STOPS_LAYER) as MockLayer).paint!["circle-color"]),
    ).toContain("#d92121");

    // Simulate the cascade resolving the dark value, then toggle the theme.
    act(() => {
      document.documentElement.style.setProperty("--delay-severe", "#F04438");
      window.dispatchEvent(new CustomEvent("themechange", { detail: "dark" }));
    });

    expect(
      JSON.stringify((map.getLayer(ROUTE_STOPS_LAYER) as MockLayer).paint!["circle-color"]),
    ).toContain("#F04438");
  });
});
