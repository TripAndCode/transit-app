import { describe, it, expect, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap, type MockLayer } from "../../test/mockMap";
import { useHeatmapLayer, SOURCE, LAYER, CLUSTER_LAYER } from "./useHeatmapLayer";
import type { HeatmapCollection } from "../../api/types";
import type { SeverityKey } from "../../components/MapLegend";

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
    useHeatmapLayer(mapRef, DATA, true, null, 1, 0);
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

  it("attaches the cluster source via the idle backstop after a style reload", () => {
    // Reproduces the locale/basemap-switch regression on the heatmap hook: the
    // style was just reloaded (isStyleLoaded() false, tiles loading) and only
    // `idle` signals readiness. The clusters must still attach, not vanish.
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    run(map);
    expect(map.getSource(SOURCE)).toBeUndefined(); // nothing while loading
    map.settleViaIdle();
    expect(map.getSource(SOURCE)).toBeDefined();
    expect(map.getLayer(CLUSTER_LAYER)).toBeDefined();
    expect(map.getLayer(LAYER)).toBeDefined();
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
      "#2EA87A", 1.5, "#C99A2E", 3, "#D4622A", 5, "#d92121",
    ]);
    // size steps on the number of stops
    expect((paint["circle-radius"] as unknown[])[1]).toEqual(["get", "point_count"]);
    // cluster bubbles render beneath the individual dots
    const ids = map.layers.map((l) => l.id);
    expect(ids.indexOf(CLUSTER_LAYER)).toBeLessThan(ids.indexOf(LAYER));
  });

  it("labels each bubble with its stop count", () => {
    const map = makeMockMap();
    run(map);
    const count = map.getLayer("delay-cluster-count") as MockLayer;
    expect(count?.type).toBe("symbol");
    expect(count.filter).toEqual(["has", "point_count"]);
    expect((count.layout as Record<string, unknown>)["text-field"]).toEqual([
      "get", "point_count_abbreviated",
    ]);
  });

  it("filters the casing + dot layers to individual (unclustered) stops only", () => {
    const map = makeMockMap();
    run(map);
    const stopFilter = ["!", ["has", "point_count"]];
    expect((map.getLayer(LAYER) as MockLayer).filter).toEqual(stopFilter);
    expect((map.getLayer("delay-casing") as MockLayer).filter).toEqual(stopFilter);
  });

  it("sizes dots by delay severity, not sample count", () => {
    const map = makeMockMap();
    run(map);
    const paint = (map.getLayer(LAYER) as MockLayer).paint as Record<string, unknown>;
    const radius = JSON.stringify(paint["circle-radius"]);
    expect(radius).toContain("avg_delay_min"); // size driven by delay
    expect(radius).not.toContain("samples"); // NOT by data volume
    // solid fill (no focus) — opacity no longer encodes samples
    expect(paint["circle-opacity"]).toBe(0.92);
  });
});

describe("useHeatmapLayer colorField", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("defaults to avg_delay_min color expression", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useHeatmapLayer(mapRef, DATA, true, null, 1, 0);
    });
    const paint = (map.getLayer(LAYER) as MockLayer).paint as Record<string, unknown>;
    expect(JSON.stringify(paint["circle-color"])).toContain("avg_delay_min");
  });

  it("recolors dots + cluster bubbles on themechange (severe band tracks the theme)", () => {
    const map = makeMockMap();
    // Start in light mode: dark is the default, so a toggle TO dark is only
    // observable from a non-dark starting DOM state. useThemeSignal reads
    // data-theme from the DOM (the source of truth applyTheme writes), so the
    // test sets it exactly as applyTheme would, alongside dispatching the event.
    document.documentElement.dataset.theme = "light";
    run(map);
    // Built with the light-mode severe red by default (no theme, jsdom cascade
    // unresolved -> severeColorResolved() falls back to #d92121).
    expect(JSON.stringify(map.getPaintProperty(LAYER, "circle-color"))).toContain("#d92121");
    expect(
      JSON.stringify((map.getLayer(CLUSTER_LAYER) as MockLayer).paint!["circle-color"]),
    ).toContain("#d92121");

    // Simulate the cascade resolving the dark value, then toggle the theme —
    // data-theme write + event, exactly what applyTheme does.
    act(() => {
      document.documentElement.style.setProperty("--delay-severe", "#F04438");
      document.documentElement.dataset.theme = "dark";
      window.dispatchEvent(new CustomEvent("themechange", { detail: "dark" }));
    });

    expect(JSON.stringify(map.getPaintProperty(LAYER, "circle-color"))).toContain("#F04438");
    expect(JSON.stringify(map.getPaintProperty(CLUSTER_LAYER, "circle-color"))).toContain("#F04438");
  });

  it("uses p90_delay_min when colorField is p90_delay_min", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useHeatmapLayer(mapRef, DATA, true, null, 1, 0, "p90_delay_min");
    });
    const paint = (map.getLayer(LAYER) as MockLayer).paint as Record<string, unknown>;
    expect(JSON.stringify(paint["circle-color"])).toContain("p90_delay_min");
  });

  it("calls setPaintProperty when colorField changes", () => {
    const map = makeMockMap();
    type Props = { field: 'avg_delay_min' | 'p90_delay_min' };
    const { rerender } = renderHook(
      ({ field }: Props) => {
        const mapRef = useRef(map as never);
        useHeatmapLayer(mapRef, DATA, true, null, 1, 0, field);
      },
      { initialProps: { field: 'avg_delay_min' } as Props },
    );
    rerender({ field: 'p90_delay_min' });
    const updated = map.getPaintProperty(LAYER, "circle-color");
    expect(JSON.stringify(updated)).toContain("p90_delay_min");
  });
});

describe("useHeatmapLayer focusedSeverity filtering (inSeverityBand)", () => {
  const BAND_DATA = {
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: { type: "Point", coordinates: [140.7, 40.8] },
        properties: { avg_delay_min: 1.0, samples: 10 } }, // ok
      { type: "Feature", geometry: { type: "Point", coordinates: [140.71, 40.81] },
        properties: { avg_delay_min: 2.0, samples: 10 } }, // mild
      { type: "Feature", geometry: { type: "Point", coordinates: [140.72, 40.82] },
        properties: { avg_delay_min: 4.0, samples: 10 } }, // moderate
      { type: "Feature", geometry: { type: "Point", coordinates: [140.73, 40.83] },
        properties: { avg_delay_min: 6.0, samples: 10 } }, // severe
    ],
  } as unknown as HeatmapCollection;

  function runFocused(map: MockMap, focusedSeverity: SeverityKey | null) {
    return renderHook(() => {
      const mapRef = useRef(map as never);
      useHeatmapLayer(mapRef, BAND_DATA, true, focusedSeverity, 1, 0);
    });
  }

  it("keeps only the ok-band stop (< 1.5min) when focused on ok", () => {
    const map = makeMockMap();
    runFocused(map, "ok");
    const src = map.getSource(SOURCE) as { data: HeatmapCollection };
    expect(src.data.features).toHaveLength(1);
    expect(src.data.features[0].properties?.avg_delay_min).toBe(1.0);
  });

  it("keeps only the mild-band stop (1.5-3min) when focused on mild", () => {
    const map = makeMockMap();
    runFocused(map, "mild");
    const src = map.getSource(SOURCE) as { data: HeatmapCollection };
    expect(src.data.features).toHaveLength(1);
    expect(src.data.features[0].properties?.avg_delay_min).toBe(2.0);
  });

  it("keeps only the moderate-band stop (3-5min) when focused on moderate", () => {
    const map = makeMockMap();
    runFocused(map, "moderate");
    const src = map.getSource(SOURCE) as { data: HeatmapCollection };
    expect(src.data.features).toHaveLength(1);
    expect(src.data.features[0].properties?.avg_delay_min).toBe(4.0);
  });

  it("keeps only the severe-band stop (>=5min) when focused on severe", () => {
    const map = makeMockMap();
    runFocused(map, "severe");
    const src = map.getSource(SOURCE) as { data: HeatmapCollection };
    expect(src.data.features).toHaveLength(1);
    expect(src.data.features[0].properties?.avg_delay_min).toBe(6.0);
  });

  // Exact boundary values — the interior-only tests above (1.0/2.0/4.0/6.0)
  // wouldn't catch a </<= flip or an off-by-a-tick shift at the real cutoffs.
  const BOUNDARY_DATA = {
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: { type: "Point", coordinates: [140.7, 40.8] },
        properties: { avg_delay_min: 1.5, samples: 10 } }, // exactly mild's lower edge
      { type: "Feature", geometry: { type: "Point", coordinates: [140.71, 40.81] },
        properties: { avg_delay_min: 3.0, samples: 10 } }, // exactly moderate's lower edge
      { type: "Feature", geometry: { type: "Point", coordinates: [140.72, 40.82] },
        properties: { avg_delay_min: 5.0, samples: 10 } }, // exactly severe's lower edge
    ],
  } as unknown as HeatmapCollection;

  function runBoundary(map: MockMap, focusedSeverity: SeverityKey | null) {
    return renderHook(() => {
      const mapRef = useRef(map as never);
      useHeatmapLayer(mapRef, BOUNDARY_DATA, true, focusedSeverity, 1, 0);
    });
  }

  it("classifies exactly 1.5min as mild, not ok (lower bound is inclusive on mild)", () => {
    const okMap = makeMockMap();
    runBoundary(okMap, "ok");
    expect((okMap.getSource(SOURCE) as { data: HeatmapCollection }).data.features).toHaveLength(0);

    const mildMap = makeMockMap();
    runBoundary(mildMap, "mild");
    const kept = (mildMap.getSource(SOURCE) as { data: HeatmapCollection }).data.features;
    expect(kept.some((f) => f.properties?.avg_delay_min === 1.5)).toBe(true);
  });

  it("classifies exactly 3.0min as moderate, not mild (lower bound is inclusive on moderate)", () => {
    const mildMap = makeMockMap();
    runBoundary(mildMap, "mild");
    const notKept = (mildMap.getSource(SOURCE) as { data: HeatmapCollection }).data.features;
    expect(notKept.some((f) => f.properties?.avg_delay_min === 3.0)).toBe(false);

    const moderateMap = makeMockMap();
    runBoundary(moderateMap, "moderate");
    const kept = (moderateMap.getSource(SOURCE) as { data: HeatmapCollection }).data.features;
    expect(kept.some((f) => f.properties?.avg_delay_min === 3.0)).toBe(true);
  });

  it("classifies exactly 5.0min as severe, not moderate (lower bound is inclusive on severe)", () => {
    const moderateMap = makeMockMap();
    runBoundary(moderateMap, "moderate");
    const notKept = (moderateMap.getSource(SOURCE) as { data: HeatmapCollection }).data.features;
    expect(notKept.some((f) => f.properties?.avg_delay_min === 5.0)).toBe(false);

    const severeMap = makeMockMap();
    runBoundary(severeMap, "severe");
    const kept = (severeMap.getSource(SOURCE) as { data: HeatmapCollection }).data.features;
    expect(kept.some((f) => f.properties?.avg_delay_min === 5.0)).toBe(true);
  });
});
