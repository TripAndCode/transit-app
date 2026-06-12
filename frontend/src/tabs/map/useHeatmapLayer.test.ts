import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap, type MockLayer } from "../../test/mockMap";
import { useHeatmapLayer, SOURCE, LAYER, HEAT_LAYER } from "./useHeatmapLayer";
import { HEAT_RAMP } from "../../styles/tokens";
import type { HeatmapCollection } from "../../api/types";

const DATA = {
  type: "FeatureCollection",
  features: [
    { type: "Feature", geometry: { type: "Point", coordinates: [140.7, 40.8] },
      properties: { avg_delay_min: 3.2, samples: 500 } },
    { type: "Feature", geometry: { type: "Point", coordinates: [140.8, 40.9] },
      properties: { avg_delay_min: 1.1, samples: 80 } },
  ],
} as unknown as HeatmapCollection;

function run(map: MockMap) {
  return renderHook(() => {
    const mapRef = useRef(map as never);
    const styleLoadedRef = useRef(true);
    useHeatmapLayer(mapRef, styleLoadedRef, DATA, true, null, 1, 0);
  });
}

describe("useHeatmapLayer", () => {
  it("adds a heatmap layer on SOURCE using HEAT_RAMP, beneath the dots", () => {
    const map = makeMockMap();
    run(map);
    const heat = map.getLayer(HEAT_LAYER) as MockLayer;
    expect(heat?.type).toBe("heatmap");
    expect(heat.source).toBe(SOURCE);
    const expected = ["interpolate", ["linear"], ["heatmap-density"], ...HEAT_RAMP.flat()];
    expect((heat.paint as Record<string, unknown>)["heatmap-color"]).toEqual(expected);
    const ids = map.layers.map((l) => l.id);
    expect(ids.indexOf(HEAT_LAYER)).toBeLessThan(ids.indexOf(LAYER));
  });

  it("fades the dots in with zoom — circle-opacity is a top-level zoom interpolate at 0 by z11", () => {
    const map = makeMockMap();
    run(map);
    const dot = map.getLayer(LAYER) as MockLayer;
    const op = (dot.paint as Record<string, unknown>)["circle-opacity"] as unknown[];
    expect(op[0]).toBe("interpolate");
    expect(op[2]).toEqual(["zoom"]); // zoom is the TOP-LEVEL input (gotcha guard)
    expect(op[3]).toBe(11); // first stop
    expect(op[4]).toBe(0); // fully transparent at overview
  });
});
