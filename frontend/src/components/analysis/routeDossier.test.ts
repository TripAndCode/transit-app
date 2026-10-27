import { describe, expect, it } from "vitest";
import type { RouteShapeStop, RouteVariant, ReportResponse, OverviewConcentration } from "../../api/types";
import { initialStop, largestRises, previousWindow, reverseVariant, routeStanding } from "./routeDossier";

function stop(stop_sequence: number, stop_name: string, avg_min: number | null): RouteShapeStop {
  return { stop_sequence, stop_name, lon: 132, lat: 34, avg_min, samples: avg_min == null ? 0 : 10 };
}

function variant(shape_id: string, first: string, last: string, share_pct: number): RouteVariant {
  return { shape_id, first_stop_name: first, last_stop_name: last, stop_count: 5, samples: 10, share_pct };
}

describe("previousWindow", () => {
  it("is the window of the same length that ends the day before", () => {
    expect(previousWindow("2026-09-24", "2026-10-23")).toEqual({ from: "2026-08-25", to: "2026-09-23" });
  });

  it("is the day before for a one-day window", () => {
    expect(previousWindow("2026-03-01", "2026-03-01")).toEqual({ from: "2026-02-28", to: "2026-02-28" });
  });
});

describe("largestRises", () => {
  const stops = [stop(1, "A", 0), stop(2, "B", 1), stop(3, "C", 2.2), stop(4, "D", 2.5), stop(5, "E", null)];

  it("names the segments where delay grows most, largest first", () => {
    const rises = largestRises(
      [
        { from_sequence: 2, to_sequence: 3, increase_min: 1.2, pairs: 30 },
        { from_sequence: 3, to_sequence: 4, increase_min: 0.3, pairs: 30 },
        { from_sequence: 4, to_sequence: 5, increase_min: 0.47, pairs: 30 },
      ],
      stops,
    );
    expect(rises.map((r) => [r.from.stop_name, r.to.stop_name, r.increaseMin])).toEqual([
      ["B", "C", 1.2],
      ["D", "E", 0.47],
    ]);
  });

  it("breaks a tie by stop order", () => {
    const rises = largestRises(
      [
        { from_sequence: 3, to_sequence: 4, increase_min: 0.5, pairs: 30 },
        { from_sequence: 2, to_sequence: 3, increase_min: 0.5, pairs: 30 },
      ],
      stops,
    );
    expect(rises.map((r) => r.from.stop_name)).toEqual(["B", "C"]);
  });

  it("leaves out a fall, a rise that rounds to nothing, and a segment whose stops are not listed", () => {
    const rises = largestRises(
      [
        { from_sequence: 2, to_sequence: 3, increase_min: -0.4, pairs: 30 },
        { from_sequence: 3, to_sequence: 4, increase_min: 0.04, pairs: 30 },
        { from_sequence: 8, to_sequence: 9, increase_min: 2, pairs: 30 },
      ],
      stops,
    );
    expect(rises).toEqual([]);
  });

  it("is empty without segments", () => {
    expect(largestRises(undefined, stops)).toEqual([]);
  });
});

describe("reverseVariant", () => {
  const variants = [
    variant("out", "広島駅", "観音新町三丁目", 31.8),
    variant("in", "観音新町三丁目", "広島駅", 25),
    variant("in-short", "観音新町三丁目", "広島駅", 0.7),
    variant("loop", "青森駅", "青森駅", 5),
  ];

  it("is the most observed variant running between the same two stops the other way", () => {
    expect(reverseVariant(variants, "out")?.shape_id).toBe("in");
    expect(reverseVariant(variants, "in-short")?.shape_id).toBe("out");
  });

  it("is null for a loop or a variant nothing runs back along", () => {
    expect(reverseVariant(variants, "loop")).toBeNull();
    expect(reverseVariant([variant("a", "X", "Y", 50)], "a")).toBeNull();
    expect(reverseVariant(variants, null)).toBeNull();
  });
});

describe("initialStop", () => {
  it("opens on the stop with the largest delay, not on a first stop that reads 0", () => {
    expect(initialStop([stop(1, "A", 0), stop(2, "B", 4.2), stop(3, "C", 5.1), stop(4, "D", null)])?.stop_name).toBe("C");
  });

  it("falls back to the first stop when nothing was observed", () => {
    expect(initialStop([stop(1, "A", null), stop(2, "B", null)])?.stop_name).toBe("A");
    expect(initialStop([])).toBeUndefined();
  });
});

function envelope(report_type: string, rows: unknown[], rows_total: number | null = null): ReportResponse {
  return { report_type, rendered_at: "", text: "", rows, rows_total, definition: {} } as unknown as ReportResponse;
}

describe("routeStanding", () => {
  const ranking = envelope("ranking", [["A", null, "5.10", "4", "9", 500], ["R", null, "3.06", "2", "8", 143516]], 174);
  const onTime = envelope("on_time", [["R", null, "49.0", "3.06", 143516, false, 0.3]]);
  const worst = envelope("worst_5min", [["R", null, 26192, "3.06", 143516]]);
  const concentration: OverviewConcentration = {
    top_routes: [{ route_code: "R", route_short_name: "3", share_pct: 5 }, { route_code: "A", route_short_name: null, share_pct: 4 }],
    rest_share_pct: 91,
  };

  it("reads the route's figures from the agency's own route lists", () => {
    expect(routeStanding("R", { ranking, onTime, worst, concentration })).toEqual({
      avgMin: 3.06,
      rank: 2,
      ranked: 174,
      onTimePct: 49,
      late5Pct: (26192 / 143516) * 100,
      sharePct: 5,
      shareRank: 1,
    });
  });

  it("reads a route no list names as having no figures", () => {
    expect(routeStanding("Z", { ranking, onTime, worst, concentration })).toEqual({
      avgMin: null, rank: null, ranked: null, onTimePct: null, late5Pct: null, sharePct: null, shareRank: null,
    });
  });

  it("reads a listed route missing from the late list as never more than 5 minutes late", () => {
    const standing = routeStanding("R", { ranking, onTime, worst: envelope("worst_5min", []), concentration });
    expect(standing.late5Pct).toBe(0);
  });

  it("takes the average from the on-time list for a route observed too rarely to rank", () => {
    const standing = routeStanding("R", { ranking: envelope("ranking", [], 0), onTime, worst, concentration });
    expect(standing).toMatchObject({ avgMin: 3.06, rank: null, ranked: null });
  });
});
