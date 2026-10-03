import { describe, it, expect } from "vitest";
import { LIGHT_DAWN, LIGHT_DUSK, LIGHT_MAX_OPACITY, LIGHT_NIGHT, LIGHT_OFF, frameHour, lightFor } from "./ambientLight";

describe("frameHour", () => {
  it("parses HH:MM to a decimal hour and rejects anything else", () => {
    expect(frameHour("17:30")).toBe(17.5);
    expect(frameHour("05:00")).toBe(5);
    expect(frameHour("24:00")).toBe(24);
    expect(frameHour("")).toBeNull();
    expect(frameHour("noon")).toBeNull();
  });
});

describe("lightFor", () => {
  it("is off when disabled or when there is no hour", () => {
    expect(lightFor(6, false)).toEqual(LIGHT_OFF);
    expect(lightFor(null, true)).toEqual(LIGHT_OFF);
  });
  it("dawn is indigo at most 8%, fading out by 07:00", () => {
    expect(lightFor(5, true)).toEqual({ color: LIGHT_DAWN, opacity: LIGHT_MAX_OPACITY.dawn });
    expect(lightFor(6, true).opacity).toBeCloseTo(0.04, 6);
    expect(lightFor(7, true)).toEqual(LIGHT_OFF);
  });
  it("daytime carries no tint", () => {
    for (const h of [7, 10, 12, 15.75]) expect(lightFor(h, true)).toEqual(LIGHT_OFF);
  });
  it("dusk is amber ramping to at most 7% by 18:00", () => {
    expect(lightFor(16, true)).toEqual({ color: LIGHT_DUSK, opacity: 0 });
    expect(lightFor(17, true).opacity).toBeCloseTo(0.035, 6);
    expect(lightFor(19, true)).toEqual({ color: LIGHT_DUSK, opacity: LIGHT_MAX_OPACITY.dusk });
  });
  it("night is indigo and never past 10%, including the 24:00 frame", () => {
    expect(lightFor(19.5, true)).toEqual({ color: LIGHT_NIGHT, opacity: 0.03 });
    expect(lightFor(22, true).opacity).toBeCloseTo(0.08, 6);
    expect(lightFor(24, true)).toEqual({ color: LIGHT_NIGHT, opacity: LIGHT_MAX_OPACITY.night });
  });
  it("every opacity it can produce stays inside the caps", () => {
    for (let h = 0; h <= 24; h += 0.25) {
      const l = lightFor(h, true);
      expect(l.opacity).toBeGreaterThanOrEqual(0);
      expect(l.opacity).toBeLessThanOrEqual(0.1);
    }
  });
});
