import { describe, it, expect } from "vitest";
import type { PipelineRun } from "../../api/admin";
import { nowMarkerHour, runsToBars } from "./runsTimeline";

// 2026-09-21 00:00 JST.
const DAY_START = new Date("2026-09-20T15:00:00Z");

function run(over: Partial<PipelineRun> & { run_id: number }): PipelineRun {
  return {
    kind: "analyze",
    agency_id: 1,
    agency_name: "Hokuriku",
    started_at: "2026-09-20T19:00:00Z",
    finished_at: "2026-09-20T19:30:00Z",
    status: "ok",
    rows: null,
    lock_probe_ms: null,
    error: null,
    requested_by: null,
    ...over,
  };
}

describe("runsToBars", () => {
  it("places a bar at its JST hour offset from the start of the day", () => {
    const [lane] = runsToBars([run({ run_id: 1 })], DAY_START, DAY_START);
    expect(lane.bars[0].startHour).toBe(4);
    expect(lane.bars[0].endHour).toBe(4.5);
  });

  it("groups runs into one lane per kind and agency", () => {
    const lanes = runsToBars(
      [
        run({ run_id: 1, kind: "ingest", agency_id: 1, agency_name: "Hokuriku" }),
        run({ run_id: 2, kind: "analyze", agency_id: 1, agency_name: "Hokuriku" }),
        run({ run_id: 3, kind: "analyze", agency_id: 2, agency_name: "Toyama" }),
        run({ run_id: 4, kind: "ingest", agency_id: 1, agency_name: "Hokuriku" }),
      ],
      DAY_START,
      DAY_START,
    );
    expect(lanes.map((l) => l.key)).toEqual(["ingest:1", "analyze:1", "analyze:2"]);
    expect(lanes[0].bars.map((b) => b.runId)).toEqual([1, 4]);
  });

  it("orders lanes by pipeline stage, then fleet-wide before per-agency", () => {
    const lanes = runsToBars(
      [
        run({ run_id: 1, kind: "weather", agency_id: null, agency_name: null }),
        run({ run_id: 2, kind: "analyze", agency_id: 2, agency_name: "Toyama" }),
        run({ run_id: 3, kind: "ingest", agency_id: null, agency_name: null }),
        run({ run_id: 4, kind: "ingest", agency_id: 2, agency_name: "Toyama" }),
      ],
      DAY_START,
      DAY_START,
    );
    expect(lanes.map((l) => l.key)).toEqual(["ingest:all", "ingest:2", "analyze:2", "weather:all"]);
  });

  it("draws an unfinished run up to now rather than leaving it zero-width", () => {
    const now = new Date("2026-09-20T20:00:00Z"); // 05:00 JST
    const [lane] = runsToBars([run({ run_id: 1, finished_at: null, status: "running" })], DAY_START, now);
    expect(lane.bars[0].endHour).toBe(5);
    expect(lane.bars[0].status).toBe("running");
  });

  it("dashes a run that never did any work, so a displaced sweep reads differently from a fast one", () => {
    const [lane] = runsToBars(
      [run({ run_id: 1, status: "skipped", finished_at: null, lock_probe_ms: 4 })],
      DAY_START,
      DAY_START,
    );
    expect(lane.bars[0].dashed).toBe(true);
  });

  it("carries the lock probe cost through under the name the API gives it", () => {
    const [lane] = runsToBars(
      [run({ run_id: 1, status: "skipped", finished_at: null, lock_probe_ms: 4 })],
      DAY_START,
      DAY_START,
    );
    expect(lane.bars[0].lockProbeMs).toBe(4);
  });

  it("leaves an ordinary completed run solid", () => {
    const [lane] = runsToBars([run({ run_id: 1 })], DAY_START, DAY_START);
    expect(lane.bars[0].dashed).toBe(false);
  });

  it("clamps a run that began before the day to the day's own axis", () => {
    const [lane] = runsToBars(
      [run({ run_id: 1, started_at: "2026-09-20T13:00:00Z", finished_at: "2026-09-20T16:00:00Z" })],
      DAY_START,
      DAY_START,
    );
    expect(lane.bars[0].startHour).toBe(0);
    expect(lane.bars[0].endHour).toBe(1);
  });

  it("drops a run that falls entirely outside the day instead of pinning it to an edge", () => {
    expect(
      runsToBars(
        [run({ run_id: 1, started_at: "2026-09-21T16:00:00Z", finished_at: "2026-09-21T17:00:00Z" })],
        DAY_START,
        DAY_START,
      ),
    ).toEqual([]);
  });

  it("keeps a lane's own bars in time order however the rows arrived", () => {
    const [lane] = runsToBars(
      [
        run({ run_id: 1, started_at: "2026-09-20T22:00:00Z", finished_at: "2026-09-20T22:10:00Z" }),
        run({ run_id: 2, started_at: "2026-09-20T18:00:00Z", finished_at: "2026-09-20T18:10:00Z" }),
      ],
      DAY_START,
      DAY_START,
    );
    expect(lane.bars.map((b) => b.runId)).toEqual([2, 1]);
  });
});

describe("nowMarkerHour", () => {
  it("is the current offset into the day being shown", () => {
    expect(nowMarkerHour(new Date("2026-09-20T23:12:00Z"), DAY_START)).toBe(8.2);
  });

  it("is null for a past day, where a moving marker would be meaningless", () => {
    expect(nowMarkerHour(new Date("2026-09-22T01:00:00Z"), DAY_START)).toBeNull();
  });
});
