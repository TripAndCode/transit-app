import { describe, expect, it } from "vitest";
import { calendarDays, dayIndexAt, rangeFrom, sinceStart } from "./brushMath";

describe("brush math", () => {
  it("maps an x offset to a day, clamped to the strip", () => {
    expect(dayIndexAt(205, 280, 28)).toBe(20);
    expect(dayIndexAt(-5, 280, 28)).toBe(0);
    expect(dayIndexAt(400, 280, 28)).toBe(27);
    expect(dayIndexAt(10, 0, 28)).toBe(0);
  });

  it("orders a drag that ends left of where it started", () => {
    expect(rangeFrom(5, 2)).toEqual([2, 5]);
    expect(rangeFrom(2, 5)).toEqual([2, 5]);
  });

  it("starts 'since collection began' no more than 365 days back", () => {
    expect(sinceStart("2026-06-01", "2026-09-28")).toBe("2026-06-01");
    expect(sinceStart("2024-01-01", "2026-09-28")).toBe("2025-09-29");
  });

  it("lists every calendar day, gaps included", () => {
    expect(calendarDays("2026-09-27", "2026-10-01")).toEqual([
      "2026-09-27",
      "2026-09-28",
      "2026-09-29",
      "2026-09-30",
      "2026-10-01",
    ]);
  });
});
