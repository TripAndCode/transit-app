import { describe, it, expect } from "vitest";
import { BREATH_WINDOW_MS, isBreathing } from "./freshness";

const now = Date.parse("2026-10-03T08:00:00Z");
describe("isBreathing", () => {
  it("breathes only while the latest observation is under two minutes old", () => {
    expect(isBreathing("2026-10-03T07:59:30Z", now)).toBe(true);
    expect(isBreathing(new Date(now - BREATH_WINDOW_MS).toISOString(), now)).toBe(false);
    expect(isBreathing("2026-10-03T07:50:00Z", now)).toBe(false);
  });
  it("never breathes on a missing, unparseable or future timestamp", () => {
    expect(isBreathing(null, now)).toBe(false);
    expect(isBreathing("soon", now)).toBe(false);
    expect(isBreathing("2026-10-03T08:00:01Z", now)).toBe(false);
  });
});
