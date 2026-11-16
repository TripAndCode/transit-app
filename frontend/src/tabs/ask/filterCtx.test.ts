import { afterEach, describe, expect, it, vi } from "vitest";
import { patchThreadFilter, resolvedFilterCtx, threadScope } from "./filterCtx";

describe("resolvedFilterCtx", () => {
  afterEach(() => vi.useRealTimers());

  it("fills a stored filter's missing dates with the default period, which stops at the data", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-02T14:59:59Z")); // 2026-10-02 23:59:59 JST
    const fc = resolvedFilterCtx({ dow: "weekend" }, "2026-09-28");
    expect(fc.from_date).toBe("2026-08-30");
    expect(fc.to_date).toBe("2026-09-28");
    expect(fc.dow).toBe("weekend");
  });

  it("keeps the dates a stored filter does carry", () => {
    const fc = resolvedFilterCtx({ from_date: "2026-06-01", to_date: "2026-06-30" }, "2026-09-28");
    expect(fc.from_date).toBe("2026-06-01");
    expect(fc.to_date).toBe("2026-06-30");
  });
});

describe("threadScope", () => {
  it("reads a stored thread filter the way a link's params are read", () => {
    const scope = threadScope({ from_date: "2026-09-01", to_date: "2026-09-07", dow: "weekday", time_band: "bogus", routes: ["R1"] }, "2026-09-30");
    expect(scope).toMatchObject({ from: "2026-09-01", to: "2026-09-07", dow: "weekday", time_band: "all", service: "all", routes: ["R1"] });
    expect(scope).toMatchObject({ hour: null, stop: null, dir: null, late: null, early: null });
  });

  it("falls back to the default period for a thread stored without dates", () => {
    expect(threadScope({}, "2026-09-30")).toMatchObject({ from: "2026-09-01", to: "2026-09-30" });
  });
});

describe("patchThreadFilter", () => {
  const stored = { from_date: "2026-09-01", to_date: "2026-09-07", dow: "weekday" as const, routes: ["R1"] };

  it("changes only the condition the patch names", () => {
    expect(patchThreadFilter(stored, { time_band: "evening" }, "2026-09-30")).toEqual({
      from_date: "2026-09-01",
      to_date: "2026-09-07",
      dow: "weekday",
      time_band: "evening",
      service: undefined,
      routes: ["R1"],
    });
  });

  it("keeps a picked set of weekdays in its canonical spelling", () => {
    expect(patchThreadFilter(stored, { dow: "wed,mon" as never }, "2026-09-30").dow).toBe("mon,wed");
  });

  it("clears the routes and restores the default period on a reset", () => {
    const next = patchThreadFilter(stored, { routes: null, from: null, to: null, dow: "all" }, "2026-09-30");
    expect(next).toMatchObject({ from_date: "2026-09-01", to_date: "2026-09-30", dow: undefined, routes: undefined });
  });
});
