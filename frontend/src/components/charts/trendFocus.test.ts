import { describe, it, expect } from "vitest";
import { isFocusDimmed, isoDow } from "./trendFocus";

describe("isoDow", () => {
  it("maps a date string to an ISO weekday (Monday = 1, Sunday = 7)", () => {
    expect(isoDow("2026-05-18")).toBe(1); // Monday
    expect(isoDow("2026-05-23")).toBe(6); // Saturday
    expect(isoDow("2026-05-24")).toBe(7); // Sunday
  });
});

describe("isFocusDimmed", () => {
  it("dims nothing while no mark holds the focus", () => {
    expect(isFocusDimmed(null, { date: "2026-05-18" }, "hourly")).toBe(false);
  });

  it("never dims the chart the focus came from", () => {
    expect(isFocusDimmed({ source: "hourly", hour: 8, dow: 1 }, { hour: 9, dow: 2 }, "hourly")).toBe(false);
  });

  it("dims a mark that disagrees on a dimension both sides carry", () => {
    expect(isFocusDimmed({ source: "daily", date: "2026-05-18", dow: 1 }, { date: "2026-05-19", hour: 8, dow: 2 }, "hourly")).toBe(true);
  });

  it("keeps a mark that agrees on every shared dimension", () => {
    expect(isFocusDimmed({ source: "daily", date: "2026-05-18", dow: 1 }, { date: "2026-05-18", hour: 8, dow: 1 }, "hourly")).toBe(false);
  });

  it("ignores a dimension only one side knows about", () => {
    // An hour row carries no date, so it narrows the daily chart by weekday
    // alone instead of dimming every day in it.
    expect(isFocusDimmed({ source: "hourly", hour: 8, dow: 3 }, { date: "2026-05-20", dow: 3 }, "daily")).toBe(false);
    expect(isFocusDimmed({ source: "hourly", hour: 8, dow: 3 }, { date: "2026-05-19", dow: 2 }, "daily")).toBe(true);
  });
});
