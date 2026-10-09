import { describe, it, expect } from "vitest";
import { PEARL_CYCLE_MS, PEARL_WIDTH, pearlGradient, pearlLine, pearlPhase } from "./pearl";
import type { LiveTripProgressResponse } from "../../api/types";

describe("pearlPhase", () => {
  it("advances 0.008 per 100 ms and wraps at one cycle", () => {
    expect(pearlPhase(0)).toBe(0);
    expect(pearlPhase(100)).toBeCloseTo(0.008, 6);
    expect(pearlPhase(PEARL_CYCLE_MS)).toBe(0);
    expect(pearlPhase(PEARL_CYCLE_MS * 1.5)).toBeCloseTo(0.5, 6);
  });
});

describe("pearlGradient", () => {
  const stopsOf = (expr: unknown[]) => expr.slice(3).filter((_, i) => i % 2 === 0) as number[];
  it("is an interpolate over line-progress, transparent except a narrow bright window at t", () => {
    const expr = pearlGradient(0.5);
    expect(expr.slice(0, 3)).toEqual(["interpolate", ["linear"], ["line-progress"]]);
    expect(expr).toContain(`rgba(255,255,255,.95)`);
    expect(stopsOf(expr)).toEqual([0, 0.5 - PEARL_WIDTH, 0.5, 0.5 + PEARL_WIDTH / 2, 1]);
  });
  it("stops stay monotonic at the end of the line", () => {
    const stops = stopsOf(pearlGradient(0.99));
    for (let i = 1; i < stops.length; i += 1) expect(stops[i]).toBeGreaterThan(stops[i - 1]);
    expect(stops[stops.length - 1]).toBe(1);
  });
  it("stops stay monotonic at the start of the line", () => {
    const stops = stopsOf(pearlGradient(0.01));
    for (let i = 1; i < stops.length; i += 1) expect(stops[i]).toBeGreaterThan(stops[i - 1]);
    expect(stops[0]).toBe(0);
  });
  it("hides entirely for a negative phase (off / reduced motion)", () => {
    const expr = pearlGradient(-1);
    expect(expr).toEqual(["interpolate", ["linear"], ["line-progress"], 0, "rgba(255,255,255,0)", 1, "rgba(255,255,255,0)"]);
  });
});

describe("pearlLine", () => {
  const stop = (seq: number, lon: number | null, lat: number | null) => ({ stop_sequence: seq, stop_id: null, stop_name: null, stop_lat: lat, stop_lon: lon, scheduled_time: null, dep_delay: 0, reported_at: "2026-10-03T00:00:00Z" });
  it("joins the reported stops in sequence order into one line", () => {
    const progress = { trip_id: "t", route_code: "3", headsign: null, direction_id: null, latest_captured_at: null, stops: [stop(2, 1, 1), stop(1, 0, 0), stop(3, null, null)] } as LiveTripProgressResponse;
    expect(pearlLine(progress)!.geometry.coordinates).toEqual([[0, 0], [1, 1]]);
  });
  it("is null below two located stops -- a point is not a segment anyone reported", () => {
    expect(pearlLine({ stops: [stop(1, 0, 0)] } as LiveTripProgressResponse)).toBeNull();
    expect(pearlLine(undefined)).toBeNull();
  });
});

describe("pearlGradient at the exact ends", () => {
  it("keeps the bright peak when it lands on a clamped edge stop", () => {
    for (const t of [0, 1]) {
      const expr = pearlGradient(t);
      const at = expr.indexOf(t, 3);
      expect(expr[at + 1]).toBe("rgba(255,255,255,.95)");
      expect(expr.filter((v) => v === t)).toHaveLength(1);
    }
  });
});
