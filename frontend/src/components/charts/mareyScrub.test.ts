import { describe, it, expect } from "vitest";
import { defaultScrubSec, interpolateLngLat, positionsAt, scrubBounds, scrubMapPositions, tripSpan, tripsCrossing } from "./mareyScrub";
import type { RouteShapeResponse, RouteShapeStop, RouteTrip } from "../../api/types";

const trip = (id: string, legs: [seq: number, sec: number | null, delay?: number][]): RouteTrip => ({
  trip_id: id, scheduled_time: null, headsign: null, avg_delay_sec: 0, samples: legs.length,
  stops: legs.map(([seq, sec, delay = 0]) => ({ stop_id: null, stop_sequence: seq, scheduled_sec: sec, observed_sec: sec == null ? null : sec + delay, delay_sec: delay })),
});
const AXIS = [{ stop_sequence: 1, stop_name: "A" }, { stop_sequence: 2, stop_name: "B" }, { stop_sequence: 3, stop_name: "C" }];

describe("tripSpan / tripsCrossing", () => {
  it("spans from the first to the last placeable observed time", () => {
    expect(tripSpan(trip("t", [[1, 25_200, 60], [2, 25_500, 120], [3, 25_800, 0]]))).toEqual({ startSec: 25_260, endSec: 25_800 });
    expect(tripSpan(trip("t", [[1, null], [2, null]]))).toBeNull();
  });
  it("sorts stops by sequence first", () => {
    expect(tripSpan(trip("t", [[3, 25_800], [1, 25_200]]))).toEqual({ startSec: 25_200, endSec: 25_800 });
  });
  it("returns the trips whose span contains the second, inclusive at both ends", () => {
    const a = trip("a", [[1, 100], [3, 200]]); const b = trip("b", [[1, 300], [3, 400]]);
    expect(tripsCrossing([a, b], 200).map((t) => t.trip_id)).toEqual(["a"]);
    expect(tripsCrossing([a, b], 300).map((t) => t.trip_id)).toEqual(["b"]);
    expect(tripsCrossing([a, b], 250)).toEqual([]);
  });
});

describe("positionsAt", () => {
  it("interpolates between the two stops the trip is between, as an axis fraction", () => {
    const [pos] = positionsAt([trip("a", [[1, 100], [2, 200], [3, 400]])], 300, AXIS);
    expect(pos).toEqual({ trip_id: "a", fromSeq: 2, toSeq: 3, f: 0.5, axisFraction: 0.75, delaySec: 0 });
  });
  it("skips legs off the axis", () => {
    expect(positionsAt([trip("a", [[1, 100], [9, 200]])], 150, AXIS)).toEqual([]);
  });
  it("passes over a leg from a stop off the axis to the next leg, rather than dropping the trip", () => {
    // At 200 the trip is exactly at stop 1, reached from stop 0, which the axis lacks.
    const [pos] = positionsAt([trip("a", [[0, 100], [1, 200], [2, 300]])], 200, AXIS);
    expect(pos).toMatchObject({ fromSeq: 1, toSeq: 2, f: 0, axisFraction: 0 });
  });
  it("sits exactly on a stop when the second equals its time", () => {
    const [pos] = positionsAt([trip("a", [[1, 100], [2, 200]])], 200, AXIS);
    expect(pos.axisFraction).toBe(0.5);
  });
});

describe("scrubBounds / defaultScrubSec", () => {
  it("steps whole minutes across the window and starts at the peak when there is one", () => {
    const w = { startSec: 6 * 3600, endSec: 10 * 3600 };
    expect(scrubBounds(w)).toEqual({ min: 21_600, max: 36_000, step: 60 });
    expect(defaultScrubSec(w, { startSec: 8 * 3600, endSec: 9 * 3600 })).toBe(28_800);
    expect(defaultScrubSec(w, null)).toBe(21_600);
  });
});

describe("interpolateLngLat", () => {
  const stops = [{ stop_sequence: 1, stop_name: "A", lon: 0, lat: 0 }, { stop_sequence: 2, stop_name: "B", lon: 2, lat: 4 }] as RouteShapeStop[];
  it("moves along the straight segment between the two stops", () => {
    expect(interpolateLngLat(stops, { trip_id: "a", fromSeq: 1, toSeq: 2, f: 0.25, axisFraction: 0, delaySec: 0 })).toEqual([0.5, 1]);
  });
  it("is null when either stop has no coordinates on the shape", () => {
    expect(interpolateLngLat(stops, { trip_id: "a", fromSeq: 2, toSeq: 3, f: 0.5, axisFraction: 0, delaySec: 0 })).toBeNull();
  });
});

describe("scrubMapPositions", () => {
  const shape = {
    route: "R1",
    geometry: null,
    stops: [{ stop_sequence: 1, stop_name: "A", lon: 132, lat: 34, avg_min: 1, samples: 5 }],
    unobserved_stops: [{ stop_sequence: 2, stop_name: "B", lon: 134, lat: 36 }],
  } as unknown as RouteShapeResponse;
  const viewWindow = { startSec: 21_600, endSec: 36_000 };

  it("places the trips the diagram draws, interpolating over observed and unobserved stops alike", () => {
    const trips = [trip("T1", [[1, 25_200], [2, 25_800]]), trip("EARLY", [[1, 20_700], [2, 25_800]])];
    expect(scrubMapPositions(trips, shape, viewWindow, 25_500)).toEqual([{ key: "T1", lon: 133, lat: 35, delaySec: 0 }]);
  });
});
