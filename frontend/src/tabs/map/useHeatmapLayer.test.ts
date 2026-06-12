import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap, type MockLayer } from "../../test/mockMap";
import { useHeatmapLayer, SOURCE, LAYER, CLUSTER_LAYER } from "./useHeatmapLayer";
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

describe("useHeatmapLayer (clustering)", () => {
  it("creates a clustered source and no heatmap layer", () => {
    const map = makeMockMap();
    run(map);
    const src = map.getSource(SOURCE) as Record<string, unknown>;
    expect(src.cluster).toBe(true);
    expect(src.clusterProperties).toEqual({ dsum: ["+", ["get", "avg_delay_min"]] });
    // the rejected heatmap layer must be gone entirely
    expect(map.layers.find((l) => l.type === "heatmap")).toBeUndefined();
    expect(map.getLayer("delay-heat")).toBeUndefined();
  });

  it("adds a cluster bubble layer colored by AVG delay, sized by count, below the dots", () => {
    const map = makeMockMap();
    run(map);
    const cluster = map.getLayer(CLUSTER_LAYER) as MockLayer;
    expect(cluster?.type).toBe("circle");
    expect(cluster.filter).toEqual(["has", "point_count"]);
    const paint = cluster.paint as Record<string, unknown>;
    // color steps on the cluster's average delay (sum / count)
    expect(paint["circle-color"]).toEqual([
      "step", ["/", ["get", "dsum"], ["get", "point_count"]],
      "#8fb88f", 2, "#d4b878", 5, "#e07a3a", 10, "#d92121",
    ]);
    // size steps on the number of stops
    expect((paint["circle-radius"] as unknown[])[1]).toEqual(["get", "point_count"]);
    // cluster bubbles render beneath the individual dots
    const ids = map.layers.map((l) => l.id);
    expect(ids.indexOf(CLUSTER_LAYER)).toBeLessThan(ids.indexOf(LAYER));
  });

  it("filters the casing + dot layers to individual (unclustered) stops only", () => {
    const map = makeMockMap();
    run(map);
    const stopFilter = ["!", ["has", "point_count"]];
    expect((map.getLayer(LAYER) as MockLayer).filter).toEqual(stopFilter);
    expect((map.getLayer("delay-casing") as MockLayer).filter).toEqual(stopFilter);
  });

  it("dots use a plain (non-zoom-faded) data-driven opacity", () => {
    const map = makeMockMap();
    run(map);
    const op = (map.getLayer(LAYER) as MockLayer).paint as Record<string, unknown>;
    // top-level op is `max` (samples/severity), NOT an `interpolate` over zoom
    expect((op["circle-opacity"] as unknown[])[0]).toBe("max");
  });
});
