import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";
import type { LiveTrip, LiveTripProgressResponse, LiveTripsResponse, RouteShapeResponse } from "../../api/types";
import { makeMockMap, type MockLayer } from "../../test/mockMap";
import { readableInkOn, severityStepColors, surfaceColorResolved } from "../../styles/tokens";
import {
  CLUSTER_RADIUS,
  LIVE_TRIPS_CLUSTER_LAYER,
  LIVE_TRIPS_CLUSTER_PROPERTIES,
  LIVE_TRIPS_LABEL_LAYER,
  LIVE_TRIPS_LAYER,
  LIVE_TRIPS_SOURCE,
  VEHICLE_RADIUS,
  labelPaint,
  clusterCirclePaint,
  clusterCountPaint,
  useOperationsMapLayers,
  vehicleCirclePaint,
} from "./useOperationsMapLayers";

function liveTrip(overrides: Partial<LiveTrip> = {}): LiveTrip {
  return {
    trip_id: "trip-1",
    route_code: "12",
    service_type: "weekday",
    scheduled_time: "10:00:00",
    dep_delay: 420,
    captured_at: "2026-09-11T01:00:00Z",
    stop_id: "S1",
    stop_sequence: 3,
    stop_name: "中央駅",
    stop_lat: 40.8,
    stop_lon: 140.7,
    headsign: "市役所前",
    ...overrides,
  };
}

const LIVE: LiveTripsResponse = {
  latest_captured_at: "2026-09-11T01:00:00Z",
  rows: [
    liveTrip(),
    liveTrip({ trip_id: "trip-without-location", stop_lat: null, stop_lon: null }),
  ],
};

const SHAPE: RouteShapeResponse = {
  route: "12",
  geometry: { type: "LineString", coordinates: [[140.7, 40.8], [140.8, 40.9]] },
  stops: [],
  unobserved_stops: [],
};

const PROGRESS: LiveTripProgressResponse = {
  trip_id: "trip-1",
  route_code: "12",
  headsign: "市役所前",
  direction_id: 0,
  latest_captured_at: "2026-09-11T01:00:00Z",
  stops: [
    { stop_sequence: 2, stop_id: "S0", stop_name: "中央", stop_lat: 40.79, stop_lon: 140.69, scheduled_time: "09:55:00", dep_delay: 60, reported_at: "2026-09-11T00:55:00Z" },
    { stop_sequence: 3, stop_id: "S1", stop_name: "中央駅", stop_lat: 40.8, stop_lon: 140.7, scheduled_time: "10:00:00", dep_delay: 420, reported_at: "2026-09-11T01:00:00Z" },
  ],
};

describe("useOperationsMapLayers", () => {
  it("maps only located TripUpdates and labels their signed delay", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useOperationsMapLayers(mapRef, LIVE, SHAPE, "12", 420, 1, 0);
    });

    const source = map.getSource(LIVE_TRIPS_SOURCE) as { data: GeoJSON.FeatureCollection<GeoJSON.Point> };
    expect(source.data.features).toHaveLength(1);
    expect(source.data.features[0].geometry.coordinates).toEqual([140.7, 40.8]);
    expect(source.data.features[0].properties).toMatchObject({
      trip_id: "trip-1",
      delay_label: "+7",
      selected: true,
    });
    expect(map.getLayer(LIVE_TRIPS_LAYER)).toBeDefined();
    expect(map.getLayer(LIVE_TRIPS_LABEL_LAYER)).toBeDefined();
  });

  it("draws the selected route with a concrete delay color", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useOperationsMapLayers(mapRef, LIVE, SHAPE, "12", 420, 1, 0);
    });

    const routeLayer = map.getLayer("active-route-line") as MockLayer;
    expect(routeLayer.paint?.["line-color"]).toBe("#A8391F");
    expect(routeLayer.paint?.["line-color"]).not.toContain("var(");
  });

  it("clusters overlapping active trips and draws the selected trip report trail", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useOperationsMapLayers(mapRef, LIVE, SHAPE, "12", 420, 1, 0, "trip-1", PROGRESS);
    });

    expect((map.getSource(LIVE_TRIPS_SOURCE) as { cluster: boolean }).cluster).toBe(true);
    const progress = map.getSource("trip-progress") as { data: GeoJSON.FeatureCollection };
    expect(progress.data.features).toHaveLength(3);
    expect(map.getLayer("trip-progress-line")).toBeDefined();
    expect(map.getLayer("trip-progress-direction")).toBeDefined();
    expect(map.getLayer("trip-progress-stops")).toBeDefined();
  });

  it("removes the route overlay when all routes are selected", () => {
    const map = makeMockMap();
    const { rerender } = renderHook(
      ({ route }: { route: string | null }) => {
        const mapRef = useRef(map as never);
        useOperationsMapLayers(mapRef, LIVE, SHAPE, route, 420, 1, 0);
      },
      { initialProps: { route: "12" as string | null } },
    );

    expect(map.getLayer("active-route-line")).toBeDefined();
    rerender({ route: null });
    expect(map.getLayer("active-route-line")).toBeUndefined();
    expect(map.getSource("active-route")).toBeUndefined();
  });
});

// The paint builders are pure functions over the resolved theme colours, so
// the expression shape can be asserted without a map at all.
describe("operations-map mark paint (pure builders)", () => {
  it("keeps per-vehicle marks small, stepping up only past the severe threshold", () => {
    expect(VEHICLE_RADIUS.base).toBeLessThanOrEqual(6);
    expect(VEHICLE_RADIUS.severe).toBeGreaterThan(VEHICLE_RADIUS.base);
    expect(VEHICLE_RADIUS.selected).toBeGreaterThan(VEHICLE_RADIUS.severe);
    expect(vehicleCirclePaint()["circle-radius"]).toEqual([
      "case",
      ["boolean", ["get", "selected"], false], VEHICLE_RADIUS.selected,
      [">=", ["/", ["get", "delay_sec"], 60], 5], VEHICLE_RADIUS.severe,
      VEHICLE_RADIUS.base,
    ]);
  });

  it("gives every vehicle mark a thin surface-derived ring instead of a heavy casing", () => {
    const paint = vehicleCirclePaint();
    expect(paint["circle-stroke-width"]).toBe(1.5);
    expect(paint["circle-stroke-color"]).toBe(surfaceColorResolved());
    expect(paint["circle-stroke-color"]).not.toContain("var(");
  });

  it("sizes clusters by point_count with a step expression", () => {
    const radius = clusterCirclePaint()["circle-radius"] as unknown[];
    expect(radius[0]).toBe("step");
    expect(radius[1]).toEqual(["get", "point_count"]);
    expect(radius.slice(2)).toEqual([
      CLUSTER_RADIUS.small,
      10, CLUSTER_RADIUS.medium,
      50, CLUSTER_RADIUS.large,
    ]);
    // Even the largest cluster stays well below the old flat 24px puck.
    expect(CLUSTER_RADIUS.large).toBeLessThan(24);
  });

  it("colours clusters by their members' MEAN delay, via the aggregated sum", () => {
    expect(LIVE_TRIPS_CLUSTER_PROPERTIES).toEqual({ delay_sum: ["+", ["get", "delay_sec"]] });
    expect(clusterCirclePaint()["circle-color"]).toEqual([
      "step",
      ["/", ["/", ["get", "delay_sum"], ["get", "point_count"]], 60],
      ...severityStepColors(),
    ]);
  });

  it("picks a cluster count ink that stays readable on every band of the ramp", () => {
    const stops = severityStepColors();
    expect(clusterCountPaint()["text-color"]).toEqual([
      "step",
      ["/", ["/", ["get", "delay_sum"], ["get", "point_count"]], 60],
      readableInkOn(stops[0]),
      stops[1], readableInkOn(stops[2]),
      stops[3], readableInkOn(stops[4]),
      stops[5], readableInkOn(stops[6]),
    ]);
  });
});

describe("operations-map layers use resolved tokens, never literal hexes", () => {
  it("builds every marker layer from the token helpers", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useOperationsMapLayers(mapRef, LIVE, SHAPE, "12", 420, 1, 0, "trip-1", PROGRESS);
    });

    expect((map.getSource(LIVE_TRIPS_SOURCE) as { clusterProperties: unknown }).clusterProperties)
      .toEqual(LIVE_TRIPS_CLUSTER_PROPERTIES);
    expect((map.getLayer(LIVE_TRIPS_CLUSTER_LAYER) as MockLayer).paint).toEqual(clusterCirclePaint());
    expect((map.getLayer(LIVE_TRIPS_LAYER) as MockLayer).paint).toEqual(vehicleCirclePaint());
    // The heavy dark casing ring under every vehicle is gone: the thin
    // surface-coloured stroke on the mark itself is what separates it now.
    expect(map.getLayer("live-trip-casing")).toBeUndefined();
    expect((map.getLayer("trip-progress-line") as MockLayer).paint?.["line-color"]).not.toBe("#2bc5aa");
    // The marker labels lost the dark casing that used to back them, so they
    // take the theme's ink and halo instead of a fixed white.
    expect((map.getLayer(LIVE_TRIPS_LABEL_LAYER) as MockLayer).paint).toEqual(labelPaint());
    expect(labelPaint()["text-color"]).not.toBe(labelPaint()["text-halo-color"]);
  });
});
