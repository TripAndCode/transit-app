import { expect, it } from "vitest";
import { matchedPrevious, orderedStops } from "./stopSeries";
import type { RouteShapeStop } from "../../api/types";
const stop: RouteShapeStop = { stop_id: "A", stop_name: "A", stop_sequence: 1, avg_min: 2, samples: 5, lon: 140, lat: 40 };
it("does not compare different stops, directions or loop visits at the same index", () => {
  expect(matchedPrevious(stop, [{ ...stop, stop_id: "B" }])).toBeNull();
  expect(matchedPrevious(stop, [{ ...stop, stop_sequence: 8 }])).toBeNull();
  expect(matchedPrevious(stop, [{ ...stop, avg_min: 0 }])).toBe(0);
  expect(matchedPrevious({ ...stop, stop_id: null }, [stop])).toBeNull();
});
it("keeps missing stops as gaps rather than zero delay", () => {
  const rows = orderedStops({ route: "1", geometry: null, stops: [{ ...stop, stop_sequence: 2 }], unobserved_stops: [{ ...stop, stop_sequence: 1 }] });
  expect(rows.map((r) => r.avg_min)).toEqual([null, 2]);
});
