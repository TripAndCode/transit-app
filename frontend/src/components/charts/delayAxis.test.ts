import { describe, it, expect } from "vitest";
import { delayAxisMax, delayAxisShare } from "./delayAxis";

describe("delayAxisMax", () => {
  it("picks the smallest 6-minute step that holds the largest figure", () => {
    expect(delayAxisMax([1.86, 1.59])).toBe(6);
    expect(delayAxisMax([5.99, 6])).toBe(6);
    expect(delayAxisMax([6.11, 2])).toBe(12);
    expect(delayAxisMax([9.01, 6.68])).toBe(12);
    expect(delayAxisMax([14.11])).toBe(18);
    expect(delayAxisMax([21.34])).toBe(24);
  });
  it("stops at 24 minutes, where a longer delay fills the bar", () => {
    expect(delayAxisMax([40])).toBe(24);
  });
  it("reads no figure, or only early and missing ones, as the first step", () => {
    expect(delayAxisMax([])).toBe(6);
    expect(delayAxisMax([null, -2])).toBe(6);
  });
});

describe("delayAxisShare", () => {
  it("scales a delay against the axis it is given and clamps to it", () => {
    expect(delayAxisShare(3, 6)).toBe(0.5);
    expect(delayAxisShare(9, 6)).toBe(1);
    expect(delayAxisShare(9, 18)).toBe(0.5);
    expect(delayAxisShare(-1, 12)).toBe(0);
    expect(delayAxisShare(null, 12)).toBe(0);
  });
});
