// @vitest-environment node
import { describe, it, expect } from "vitest";
import { TRIPS } from "./heroMapScene";
import { DURATION, LOOP_START, frameAt, type HeroFrame } from "./heroMapTimeline";

const kinds = (f: HeroFrame) => f.sections.map((s) => s.kind);
const settled = (f: HeroFrame) => f.sections.filter((s) => s.enter === 1 && s.exit === 0).map((s) => s.kind);

describe("frameAt", () => {
  it("opens on the map alone: no dots, no panel, no caption", () => {
    const f = frameAt(0);
    expect(TRIPS.every((tp) => f.tripAppear(tp.id) === 0)).toBe(true);
    expect(f.panelEnter).toBe(0);
    expect(f.sections).toHaveLength(0);
    expect(f.caption).toBeNull();
  });

  it("plays live operations → trip → playback → overview → live, in that order", () => {
    expect(settled(frameAt(4.2))).toEqual(["queue"]);
    expect(settled(frameAt(7.0))).toEqual(["trip"]);
    expect(settled(frameAt(11.5))).toEqual(["hourly"]);
    expect(settled(frameAt(15.0))).toEqual(["overview"]);
    expect(settled(frameAt(18.5))).toEqual(["queue"]);
  });

  it("hands each panel section to the next without a gap", () => {
    for (let t = 2.4; t < DURATION; t += 0.05) expect(kinds(frameAt(t)).length).toBeGreaterThan(0);
  });

  it("flies the trail into the trip chart only while the trip section is up", () => {
    for (const t of [6.9, 7.3, 7.7]) {
      const f = frameAt(t);
      expect(f.trailMorph).toBeGreaterThan(0);
      expect(f.trailMorph).toBeLessThan(1);
      expect(settled(f)).toContain("trip");
    }
  });

  it("flies the rail's hours into the bars only during playback", () => {
    const f = frameAt(10.4);
    expect(f.hourMorph).toBeGreaterThan(0);
    expect(f.hourMorph).toBeLessThan(1);
    expect(f.playback.railAlpha).toBe(1);
  });

  it("hides trip dots during playback, which shows stop circles instead", () => {
    const f = frameAt(11.5);
    expect(f.tripsAlpha).toBe(0);
    expect(f.playback.stopsAlpha).toBe(1);
  });

  it("jumps trips to their next stop at the refresh instead of gliding them", () => {
    expect(frameAt(3.2).tripsAtNextStop).toBe(false);
    expect(frameAt(3.4).tripsBlinking).toBe(true);
    expect(frameAt(3.6).tripsAtNextStop).toBe(true);
  });
});

describe("loop seam", () => {
  it("settles every first-play motion before LOOP_START", () => {
    const f = frameAt(LOOP_START);
    expect(f.panelEnter).toBe(1);
    expect(f.refreshChip).toBe(0);
    expect(f.tripPop).toBe(1);
    expect(TRIPS.every((tp) => f.tripAppear(tp.id) === 1)).toBe(true);
  });

  it("ends on the same visible state it loops back to", () => {
    const end = frameAt(DURATION - 1e-6);
    const start = frameAt(LOOP_START);
    for (const key of ["zoom", "tripsAtNextStop", "tripsBlinking", "tripsAlpha", "routeReveal", "trailReveal", "refreshChip", "panelEnter"] as const) {
      expect(end[key]).toBe(start[key]);
    }
    for (const key of ["x", "y", "z", "yaw", "pitch", "focal"] as const) expect(end.camera[key]).toBeCloseTo(start.camera[key]);
    expect(settled(end)).toEqual(settled(start));
    expect(end.caption?.index).toBe(start.caption?.index);
  });

  it("finishes the returning queue's counters and rows before the loop", () => {
    // The panel's last queue row lands 1.1 s + two half-beats into the section.
    const lastRowLands = 1.1 + 2 * (60 / 128 / 2);
    const queue = frameAt(DURATION - 1e-6).sections.find((s) => s.kind === "queue")!;
    expect(queue.since).toBeGreaterThan(lastRowLands);
  });
});
