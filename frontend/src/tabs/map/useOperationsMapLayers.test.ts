import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";
import type { LiveTrip, LiveTripProgressResponse, LiveTripsResponse, RouteShapeResponse } from "../../api/types";
import { makeMockMap, type MockLayer } from "../../test/mockMap";
import {
  LIVE_TRIPS_LABEL_LAYER,
  LIVE_TRIPS_LAYER,
  LIVE_TRIPS_SOURCE,
  useOperationsMapLayers,
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
    expect(routeLayer.paint?.["line-color"]).toBe("#d92121");
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
