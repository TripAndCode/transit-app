import { describe, it, expect } from "vitest";
import { isoDow } from "./trendFocus";

describe("isoDow", () => {
  it("maps a date string to an ISO weekday (Monday = 1, Sunday = 7)", () => {
    expect(isoDow("2026-05-18")).toBe(1); // Monday
    expect(isoDow("2026-05-23")).toBe(6); // Saturday
    expect(isoDow("2026-05-24")).toBe(7); // Sunday
  });
});
