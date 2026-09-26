// @vitest-environment node
import { describe, it, expect } from "vitest";
import { buildHeroMap } from "./heroMapScene";
import { SEQUENCE_END, cameraForTime, frameAt } from "./heroMapTimeline";

const towerCount = buildHeroMap().towers.length;
const allTowers = (t: number) => Array.from({ length: towerCount }, (_, i) => frameAt(t).towerGrowth(i));

describe("frameAt", () => {
  it("opens on an empty map: no routes, towers, callout or caption yet", () => {
    const f = frameAt(0);
    expect(f.routeProgress(0)).toBe(0);
    expect(allTowers(0).every((g) => g === 0)).toBe(true);
    expect(f.calloutAlpha).toBe(0);
    expect(f.caption).toBeNull();
  });

  it("plays the acts in order: routes, then delay tint and callout, then towers", () => {
    expect(frameAt(4.4).routeProgress(0)).toBe(1);
    expect(frameAt(4.4).delayTint).toBe(0);
    expect(frameAt(7.5).calloutAlpha).toBe(1);
    expect(allTowers(7.5).every((g) => g === 0)).toBe(true);
    expect(frameAt(9.5).calloutAlpha).toBe(0);
    expect(allTowers(9.5).some((g) => g > 0)).toBe(true);
  });

  it("shows one caption per act", () => {
    expect(frameAt(3).caption?.index).toBe(0);
    expect(frameAt(6).caption?.index).toBe(1);
    expect(frameAt(10.5).caption?.index).toBe(2);
  });

  it("holds the finished state after the script instead of looping back", () => {
    for (const t of [SEQUENCE_END + 5, SEQUENCE_END + 600]) {
      const f = frameAt(t);
      expect(allTowers(t).every((g) => g === 1)).toBe(true);
      expect(f.routeProgress(5)).toBe(1);
      expect(f.towerLabelAlpha).toBe(1);
      expect(f.caption).toBeNull();
    }
  });

  it("draws a complete scene for the reduced-motion still", () => {
    const f = frameAt(SEQUENCE_END);
    expect(allTowers(SEQUENCE_END).every((g) => g === 1)).toBe(true);
    expect(f.towerLabelAlpha).toBe(1);
    expect(f.legendAlpha).toBe(1);
  });
});

describe("cameraForTime", () => {
  it("hands off from the scripted path to the orbit without a jump", () => {
    const before = cameraForTime(SEQUENCE_END - 1e-6);
    const after = cameraForTime(SEQUENCE_END + 1e-6);
    for (const key of ["x", "y", "z", "yaw", "pitch"] as const) expect(after[key]).toBeCloseTo(before[key], 3);
  });

  it("keeps the idle orbit within a bounded swing, however long the page stays open", () => {
    const yaws = Array.from({ length: 200 }, (_, i) => cameraForTime(SEQUENCE_END + i * 7).yaw);
    const start = cameraForTime(SEQUENCE_END).yaw;
    for (const yaw of yaws) expect(Math.abs(yaw - start)).toBeLessThanOrEqual(0.35 + 1e-9);
  });
});
