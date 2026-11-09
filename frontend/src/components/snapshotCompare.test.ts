import { describe, expect, it } from "vitest";
import type { DefinitionMeta, ReportResponse } from "../api/types";
import { compareCouncilTiles, compareRouteTables, differenceCount, sameFigure, sameOnTimeWindow } from "./snapshotCompare";

type RouteTableReport = Extract<ReportResponse, { report_type: "route_table" }>;
type RouteTableRow = RouteTableReport["rows"][number];
type CouncilReport = Extract<ReportResponse, { report_type: "council_summary" }>;

const DEFINITION: DefinitionMeta = {
  preset: "standard_m1_p5",
  early_tolerance_sec: 60,
  late_tolerance_sec: 300,
  early_departure_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: "2026-11-05T15:30:00Z",
  value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

function row(code: string, avg: string, onTime: string, samples = 1236, early = "3.5"): RouteTableRow {
  return [code, avg, onTime, "40.1", 496, samples, early, false, 1.9];
}

function table(rows: RouteTableRow[], overrides: Partial<RouteTableReport> = {}): RouteTableReport {
  return {
    report_type: "route_table",
    rendered_at: "2026-11-09T05:05:00Z",
    text: "",
    rows,
    definition: DEFINITION,
    rows_total: rows.length,
    ...overrides,
  };
}

function council(overrides: Partial<CouncilReport> = {}, figures: Partial<Record<number, number | null>> = {}): CouncilReport {
  const values: CouncilReport["rows"][number] = [22.6, 2.5, 540357, 2130, 2100, 98.6, 4.2];
  for (const [index, value] of Object.entries(figures)) values[Number(index)] = value as never;
  return {
    report_type: "council_summary",
    rendered_at: "2026-11-09T05:05:00Z",
    text: "On time 22.6% of the time.",
    rows: [values],
    definition: DEFINITION,
    covered_days: 31,
    window_days: 31,
    delivered_missing: null,
    comparability: null,
    ...overrides,
  };
}

describe("sameFigure", () => {
  it("compares numbers at the precision they print with, whether sent as numbers or decimal strings", () => {
    expect(sameFigure("10.60", 10.6, 1)).toBe(true);
    expect(sameFigure("10.60", "10.64", 1)).toBe(true);
    expect(sameFigure("10.60", "10.66", 1)).toBe(false);
    expect(sameFigure(496, 497, 0)).toBe(false);
  });

  it("treats a missing figure as different from any number, and equal only to another missing one", () => {
    expect(sameFigure(null, 0, 1)).toBe(false);
    expect(sameFigure(12.3, null, 1)).toBe(false);
    expect(sameFigure(null, null, 1)).toBe(true);
    expect(sameFigure(undefined, null, 1)).toBe(true);
  });
});

describe("compareRouteTables", () => {
  it("leaves a route whose every figure prints the same unmarked", () => {
    const { rows, marks } = compareRouteTables(table([row("42", "10.60", "12.3")]), table([row("42", "10.6", "12.3")]));
    expect(rows).toEqual([row("42", "10.60", "12.3")]);
    expect(marks).toEqual([{ side: "both", today: new Map() }]);
    expect(differenceCount(marks)).toBe(0);
  });

  it("carries today's figure for each column whose figure changed, keeping the snapshot's row", () => {
    const { rows, marks } = compareRouteTables(table([row("42", "10.60", "12.3", 1236)]), table([row("42", "11.20", "12.3", 1301)]));
    expect(rows[0]).toEqual(row("42", "10.60", "12.3", 1236));
    expect(marks[0]).toEqual({ side: "both", today: new Map<number, unknown>([[1, "11.20"], [5, 1301]]) });
    expect(differenceCount(marks)).toBe(2);
  });

  it("marks a route only one side lists, keeping the snapshot's order and adding today's routes after it", () => {
    const { rows, marks, capped } = compareRouteTables(
      table([row("7", "3.0", "80.0"), row("42", "10.6", "12.3")]),
      table([row("9", "4.0", "70.0"), row("42", "10.6", "12.3")]),
    );
    expect(rows.map((r) => r[0])).toEqual(["7", "42", "9"]);
    expect(marks.map((m) => m.side)).toEqual(["snapshot", "both", "today"]);
    expect(rows[2]).toEqual(row("9", "4.0", "70.0"));
    expect(differenceCount(marks)).toBe(2);
    expect(capped).toBe(false);
  });

  it("says a row limit left routes out when either side listed fewer routes than qualified", () => {
    const stored = table([row("42", "10.6", "12.3")], { rows_total: 174 });
    expect(compareRouteTables(stored, table([row("42", "10.6", "12.3")])).capped).toBe(true);
    expect(compareRouteTables(table([row("42", "10.6", "12.3")]), stored).capped).toBe(true);
  });

  it("compares no early-departure rate the snapshot's table never showed", () => {
    const stored = table([row("42", "10.6", "12.3", 1236, "3.5")], { definition: { ...DEFINITION, early_departure_sec: null } });
    const { marks } = compareRouteTables(stored, table([row("42", "10.6", "12.3", 1236, "9.9")]));
    expect(differenceCount(marks)).toBe(0);
    const shown = compareRouteTables(table([row("42", "10.6", "12.3", 1236, "3.5")]), table([row("42", "10.6", "12.3", 1236, "9.9")]));
    expect(shown.marks[0]).toEqual({ side: "both", today: new Map<number, unknown>([[6, "9.9"]]) });
  });
});

function gate(overrides: Partial<NonNullable<CouncilReport["comparability"]>> = {}): NonNullable<CouncilReport["comparability"]> {
  return {
    comparable: true,
    reason: null,
    coverage_pct: 80,
    observed_trips: 1700,
    coverage_planned_trips: 2130,
    ci_low: 20,
    ci_high: 25,
    clamp_pct: 0.1,
    planned_trips: 2130,
    delivered_missing: null,
    thresholds: { min_coverage_pct: 50, max_ci_half_width_pp: 5, max_clamp_pct: 5 },
    ...overrides,
  };
}

describe("compareCouncilTiles", () => {
  it("finds nothing when every printed figure matches, whatever the prose says", () => {
    expect(compareCouncilTiles(council(), council({ text: "Prose written again." }))).toEqual(new Map());
  });

  it("marks the tile of each figure that moved", () => {
    const today = council({ covered_days: 30 }, { 0: 23.1, 2: 541000 });
    expect(compareCouncilTiles(council(), today)).toEqual(
      new Map([
        ["on_time", "changed"],
        ["observations", "changed"],
        ["planned", "changed"],
      ]),
    );
  });

  it("treats a figure missing on one side as moved", () => {
    expect(compareCouncilTiles(council(), council({ delivered_missing: "unreadable" }, { 5: null }))).toEqual(new Map([["delivered", "changed"]]));
    expect(compareCouncilTiles(council({}, { 0: null }), council())).toEqual(new Map([["on_time", "changed"]]));
  });

  it("compares no early-departure tile the snapshot never showed", () => {
    const stored = council({ definition: { ...DEFINITION, early_departure_sec: null } });
    expect(compareCouncilTiles(stored, council({}, { 6: 9.9 }))).toEqual(new Map());
    expect(compareCouncilTiles(council(), council({}, { 6: 9.9 }))).toEqual(new Map([["early", "changed"]]));
  });

  it("compares the board's verdict, with every figure its reason prints, and a verdict only one side has", () => {
    expect(compareCouncilTiles(council({ comparability: gate() }), council({ comparability: gate() }))).toEqual(new Map());
    const low = (coverage: number) => gate({ comparable: false, reason: "coverage_low", coverage_pct: coverage });
    expect(compareCouncilTiles(council({ comparability: gate() }), council({ comparability: low(45) }))).toEqual(new Map([["comparison", "changed"]]));
    expect(compareCouncilTiles(council({ comparability: low(45.0) }), council({ comparability: low(48.2) }))).toEqual(new Map([["comparison", "changed"]]));
    expect(compareCouncilTiles(council({ comparability: low(45.0) }), council({ comparability: low(45.04) }))).toEqual(new Map());
    // Under a coverage reason, the clamp share is not printed.
    expect(compareCouncilTiles(council({ comparability: low(45) }), council({ comparability: { ...low(45), clamp_pct: 3 } }))).toEqual(new Map());
    expect(compareCouncilTiles(council({ comparability: gate() }), council())).toEqual(new Map([["comparison", "snapshot"]]));
    expect(compareCouncilTiles(council(), council({ comparability: gate() }))).toEqual(new Map([["comparison", "today"]]));
  });
});

describe("sameOnTimeWindow", () => {
  it("compares the window the figures counted on time by, not the preset's name alone", () => {
    expect(sameOnTimeWindow(DEFINITION, { ...DEFINITION, method_version: "2026.12.0" })).toBe(true);
    expect(sameOnTimeWindow(DEFINITION, { ...DEFINITION, preset: "legacy_60s", early_tolerance_sec: null, late_tolerance_sec: 60 })).toBe(false);
    expect(sameOnTimeWindow(DEFINITION, { ...DEFINITION, late_tolerance_sec: 120, preset: null })).toBe(false);
  });
});
