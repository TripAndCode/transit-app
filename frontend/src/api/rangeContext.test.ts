import { describe, it, expect } from "vitest";
import {
  ctxToQueryString,
  toJstISO,
  isoDaysAgo,
  jstYearMonth,
  type RangeCtx,
} from "./rangeContext";

function makeCtx(overrides: Partial<RangeCtx> = {}): RangeCtx {
  return {
    from: "2024-01-01",
    to: "2024-01-31",
    dow: "all",
    time_band: "all",
    service: "all",
    routes: [],
    ...overrides,
  };
}

describe("ctxToQueryString", () => {
  it("always includes from and to", () => {
    const qs = new URLSearchParams(ctxToQueryString(makeCtx()));
    expect(qs.get("from")).toBe("2024-01-01");
    expect(qs.get("to")).toBe("2024-01-31");
  });

  it('omits dow, time_band, and service when they are "all"', () => {
    const qs = new URLSearchParams(ctxToQueryString(makeCtx()));
    expect(qs.has("dow")).toBe(false);
    expect(qs.has("time_band")).toBe(false);
    expect(qs.has("service")).toBe(false);
  });

  it("includes non-default filter values", () => {
    const qs = new URLSearchParams(
      ctxToQueryString(
        makeCtx({ dow: "weekday", time_band: "morning", service: "平日" }),
      ),
    );
    expect(qs.get("dow")).toBe("weekday");
    expect(qs.get("time_band")).toBe("morning");
    expect(qs.get("service")).toBe("平日");
  });

  it("omits routes when empty and joins them with commas when present", () => {
    expect(new URLSearchParams(ctxToQueryString(makeCtx())).has("routes")).toBe(false);
    const qs = new URLSearchParams(
      ctxToQueryString(makeCtx({ routes: ["A1", "B2", "C3"] })),
    );
    expect(qs.get("routes")).toBe("A1,B2,C3");
  });
});

describe("JST date helpers", () => {
  it("formats a Date as YYYY-MM-DD in JST", () => {
    // 2024-03-10T15:30:00Z is 2024-03-11 00:30 JST (UTC+9).
    expect(toJstISO(new Date("2024-03-10T15:30:00Z"))).toBe("2024-03-11");
  });

  it("returns a calendar date `days` before today in ISO form", () => {
    const today = toJstISO(new Date());
    const sevenAgo = isoDaysAgo(7);
    expect(sevenAgo).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(sevenAgo < today).toBe(true);
  });

  it("derives the JST year and 1-based month", () => {
    // 2023-12-31T16:00:00Z is 2024-01-01 01:00 JST → year rolls over.
    expect(jstYearMonth(new Date("2023-12-31T16:00:00Z"))).toEqual({
      year: 2024,
      month: 1,
    });
  });
});
