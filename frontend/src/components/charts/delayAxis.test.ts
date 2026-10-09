import { describe, it, expect } from "vitest";
import { DELAY_AXIS_MAX_MIN, delayAxisShare } from "./delayAxis";

describe("delayAxisShare", () => {
  it("scales a delay against the fixed 6-minute axis and clamps to it", () => {
    expect(DELAY_AXIS_MAX_MIN).toBe(6);
    expect(delayAxisShare(3)).toBe(0.5);
    expect(delayAxisShare(9)).toBe(1);
    expect(delayAxisShare(-1)).toBe(0);
    expect(delayAxisShare(null)).toBe(0);
  });
});
