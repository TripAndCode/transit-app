import { describe, expect, it } from "vitest";
import type { DefinitionMeta, ReportResponse } from "../api/types";
import { changedCouncilTiles, compareRouteTables, differenceCount, sameFigure, sameOnTimeWindow } from "./snapshotCompare";

type RouteTableRow = Extract<ReportResponse, { report_type: "route_table" }>["rows"][number];
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

function row(code: string, avg: string, onTime: string, samples = 1236): RouteTableRow {
  return [code, avg, onTime, "40.1", 496, samples, "3.5", false, 1.9];
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
    const { rows, marks } = compareRouteTables([row("42", "10.60", "12.3")], [row("42", "10.6", "12.3")]);
    expect(rows).toEqual([row("42", "10.60", "12.3")]);
    expect(marks).toEqual([{ side: "both", today: new Map() }]);
    expect(differenceCount(marks)).toBe(0);
  });

  it("carries today's figure for each column whose figure changed, keeping the snapshot's row", () => {
    const { rows, marks } = compareRouteTables([row("42", "10.60", "12.3", 1236)], [row("42", "11.20", "12.3", 1301)]);
    expect(rows[0]).toEqual(row("42", "10.60", "12.3", 1236));
    expect(marks[0]).toEqual({ side: "both", today: new Map<number, unknown>([[1, "11.20"], [5, 1301]]) });
    expect(differenceCount(marks)).toBe(2);
  });

  it("marks a route only one side lists, keeping the snapshot's order and adding today's routes after it", () => {
    const { rows, marks } = compareRouteTables(
      [row("7", "3.0", "80.0"), row("42", "10.6", "12.3")],
      [row("9", "4.0", "70.0"), row("42", "10.6", "12.3")],
    );
    expect(rows.map((r) => r[0])).toEqual(["7", "42", "9"]);
    expect(marks.map((m) => m.side)).toEqual(["snapshot", "both", "today"]);
    expect(rows[2]).toEqual(row("9", "4.0", "70.0"));
    expect(differenceCount(marks)).toBe(2);
  });
});

describe("changedCouncilTiles", () => {
  it("finds nothing when every structured figure matches, whatever the prose says", () => {
    expect(changedCouncilTiles(council(), council({ text: "Prose written again." }))).toEqual(new Set());
  });

  it("names the tile of each figure that moved", () => {
    const today = council({ covered_days: 30 }, { 0: 23.1, 2: 541000 });
    expect(changedCouncilTiles(council(), today)).toEqual(new Set(["on_time", "observations", "planned"]));
  });

  it("treats a figure missing on one side as moved", () => {
    expect(changedCouncilTiles(council(), council({ delivered_missing: "unreadable" }, { 5: null }))).toEqual(new Set(["delivered"]));
    expect(changedCouncilTiles(council({}, { 0: null }), council())).toEqual(new Set(["on_time"]));
  });

  it("compares the board's verdict, including one only a side has", () => {
    const gate = (comparable: boolean) => ({
      comparable,
      reason: comparable ? null : ("coverage_low" as const),
      coverage_pct: 80,
      observed_trips: 1700,
      coverage_planned_trips: 2130,
      ci_low: 20,
      ci_high: 25,
      clamp_pct: 0.1,
      planned_trips: 2130,
      delivered_missing: null,
      thresholds: { min_coverage_pct: 50, max_ci_half_width_pp: 5, max_clamp_pct: 5 },
    });
    expect(changedCouncilTiles(council({ comparability: gate(true) }), council({ comparability: gate(true) }))).toEqual(new Set());
    expect(changedCouncilTiles(council({ comparability: gate(true) }), council({ comparability: gate(false) }))).toEqual(new Set(["comparison"]));
    expect(changedCouncilTiles(council({ comparability: gate(true) }), council())).toEqual(new Set(["comparison"]));
  });
});

describe("sameOnTimeWindow", () => {
  it("compares the window the figures counted on time by, not the preset's name alone", () => {
    expect(sameOnTimeWindow(DEFINITION, { ...DEFINITION, method_version: "2026.12.0" })).toBe(true);
    expect(sameOnTimeWindow(DEFINITION, { ...DEFINITION, preset: "legacy_60s", early_tolerance_sec: null, late_tolerance_sec: 60 })).toBe(false);
    expect(sameOnTimeWindow(DEFINITION, { ...DEFINITION, late_tolerance_sec: 120, preset: null })).toBe(false);
  });
});
