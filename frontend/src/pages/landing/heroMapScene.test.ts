// @vitest-environment node
import { describe, it, expect } from "vitest";
import { buildHeroMap, coastZ, polyline } from "./heroMapScene";

describe("polyline", () => {
  it("measures arc length and interpolates by it", () => {
    const line = polyline([
      [0, 0],
      [3, 0],
      [3, 4],
    ]);
    expect(line.length).toBe(7);
    expect(line.at(0)).toEqual([0, 0]);
    expect(line.at(1)).toEqual([3, 4]);
    expect(line.at(3 / 7)[0]).toBeCloseTo(3);
    expect(line.at(-1)).toEqual([0, 0]);
    expect(line.at(2)).toEqual([3, 4]);
  });
});

describe("buildHeroMap", () => {
  const map = buildHeroMap();

  it("is deterministic across builds", () => {
    const again = buildHeroMap();
    expect(again.towers).toEqual(map.towers);
    expect(again.hotspot).toEqual(map.hotspot);
  });

  it("puts every station and stop tower on land, never in the harbor", () => {
    for (const s of map.stations) expect(s.z).toBeGreaterThan(coastZ(s.x));
    for (const t of map.towers) expect(t.z).toBeGreaterThan(coastZ(t.x));
  });

  it("runs all three modes", () => {
    expect(new Set(map.routes.map((r) => r.mode))).toEqual(new Set(["bus", "train", "tram"]));
  });

  it("points the callout and the tallest tower at the same place and figure", () => {
    expect(map.tallestTower.x).toBeCloseTo(map.hotspot.x);
    expect(map.tallestTower.z).toBeCloseTo(map.hotspot.z);
    expect(map.tallestTower.delay).toBeCloseTo(map.hotspot.delay);
    expect(Math.max(...map.towers.map((t) => t.delay))).toBe(map.tallestTower.delay);
  });

  it("keeps rail steadier than buses, as the story claims", () => {
    const maxDelay = (mode: string) =>
      Math.max(
        ...map.routes.filter((r) => r.mode === mode).flatMap((r) => Array.from({ length: 21 }, (_, i) => r.delayAt(i / 20))),
      );
    expect(maxDelay("train")).toBeLessThan(maxDelay("tram"));
    expect(maxDelay("tram")).toBeLessThan(maxDelay("bus"));
  });
});
