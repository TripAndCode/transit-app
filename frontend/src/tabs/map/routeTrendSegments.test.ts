import { describe, it, expect } from "vitest";
import { buildTrendSegments } from "./routeTrendSegments";
import type { RouteShapeStop, UnobservedStop } from "../../api/types";

function stop(seq: number, avgMin: number, samples: number): RouteShapeStop {
  return { stop_sequence: seq, stop_name: `Stop ${seq}`, lon: seq, lat: seq, avg_min: avgMin, samples };
}
function unobserved(seq: number): UnobservedStop {
  return { stop_sequence: seq, stop_name: `Stop ${seq}`, lon: seq, lat: seq };
}

describe("buildTrendSegments", () => {
  it("builds stops.length - 1 segments when there are no unobserved gaps", () => {
    const stops = [stop(1, 1.0, 20), stop(2, 3.0, 20), stop(3, 6.0, 20)];
    expect(buildTrendSegments(stops, [])).toHaveLength(2);
  });

  it("colors each segment by the SECOND (arriving) stop's avg_min", () => {
    const stops = [stop(1, 1.0, 20), stop(2, 5.0, 20)];
    const segments = buildTrendSegments(stops, []);
    expect(segments[0].properties.avg_min).toBe(5.0);
    expect(segments[0].properties.has_data).toBe(true);
    expect(segments[0].geometry.coordinates).toEqual([[1, 1], [2, 2]]);
  });

  it("marks a segment leading into an unobserved stop as has_data: false, avg_min: 0", () => {
    const stops = [stop(1, 1.0, 20)];
    const segments = buildTrendSegments(stops, [unobserved(2)]);
    expect(segments).toHaveLength(1);
    expect(segments[0].properties).toEqual({ avg_min: 0, has_data: false });
  });

  it("merges stops and unobserved stops by stop_sequence, not by array order", () => {
    // sequence 1 (observed), 2 (unobserved), 3 (observed) — passed as two
    // separate arrays, must still produce segments in sequence order.
    const stops = [stop(1, 1.0, 20), stop(3, 9.0, 20)];
    const segments = buildTrendSegments(stops, [unobserved(2)]);
    expect(segments).toHaveLength(2);
    expect(segments[0].properties.has_data).toBe(false); // 1 -> 2 (unobserved)
    expect(segments[1].properties.avg_min).toBe(9.0); // 2 -> 3
  });

  it("treats a stop with samples: 0 as has_data: false, avg_min: 0 (matches existing dot convention)", () => {
    const stops = [stop(1, 1.0, 20), stop(2, 999, 0)];
    const segments = buildTrendSegments(stops, []);
    expect(segments[0].properties).toEqual({ avg_min: 0, has_data: false });
  });

  it("returns an empty array when fewer than 2 stops are merged", () => {
    expect(buildTrendSegments([stop(1, 1.0, 20)], [])).toEqual([]);
    expect(buildTrendSegments([], [])).toEqual([]);
  });
});
