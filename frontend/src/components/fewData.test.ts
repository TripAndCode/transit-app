import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { FEW_DATA_MIN_SAMPLES, isFewData } from "./fewData";

describe("FEW_DATA_MIN_SAMPLES", () => {
  it("is the server's ranking floor", () => {
    const source = readFileSync(path.resolve(__dirname, "../../../pipeline/reports/rankings.py"), "utf8");
    expect(Number(/^RANKING_MIN_SAMPLES = (\d+)$/m.exec(source)?.[1])).toBe(FEW_DATA_MIN_SAMPLES);
  });
});

describe("isFewData", () => {
  it("marks a count under the floor and nothing at or over it", () => {
    expect(isFewData(99, FEW_DATA_MIN_SAMPLES)).toBe(true);
    expect(isFewData(100, FEW_DATA_MIN_SAMPLES)).toBe(false);
    expect(isFewData(30, 21)).toBe(false);
  });

  it("claims nothing without a count or a floor", () => {
    expect(isFewData(null, FEW_DATA_MIN_SAMPLES)).toBe(false);
    expect(isFewData(5, null)).toBe(false);
  });
});
