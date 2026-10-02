// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  applyScopePatch,
  explicitScopeQuery,
  isoDaysAgo,
  isoDaysBefore,
  jstYearMonth,
  parseScope,
  presetScopePatch,
  scopeToQueryString,
  toJstISO,
} from "./scope";

const DEFAULTS = { from: "2026-09-01", to: "2026-09-30" };
const parse = (qs: string) => parseScope(new URLSearchParams(qs), DEFAULTS);

describe("parseScope", () => {
  it("fills defaults for an empty URL", () => {
    expect(parse("")).toEqual({
      from: "2026-09-01", to: "2026-09-30", dow: "all", time_band: "all", service: "all", routes: [],
      hour: null, stop: null, dir: null, late: null, early: null,
    });
  });

  it("reads every scope param", () => {
    const s = parse("from=2026-08-20&to=2026-09-18&dow=mon,wed&service=%E5%B9%B3%E6%97%A5&routes=50,51&hour=7-9&stop=S1&dir=1&late=180&early=30");
    expect(s).toMatchObject({ dow: "mon,wed", service: "平日", routes: ["50", "51"], hour: [7, 9], stop: "S1", dir: 1, late: 180, early: 30 });
  });

  it("canonicalises weekday lists the way the API does", () => {
    expect(parse("dow=wed,mon").dow).toBe("mon,wed");
    expect(parse("dow=mon,tue,wed,thu,fri").dow).toBe("weekday");
    expect(parse("dow=sun,sat").dow).toBe("weekend");
    expect(parse("dow=mon,tue,wed,thu,fri,sat,sun").dow).toBe("all");
  });

  it("keeps hour and drops time_band when a link carries both", () => {
    const s = parse("hour=7&time_band=morning");
    expect(s.hour).toEqual([7, 7]);
    expect(s.time_band).toBe("all");
  });

  it("falls back to the default for a hand-edited invalid value", () => {
    const s = parse("from=2026-13-40&dow=funday&time_band=brunch&service=x&hour=25&dir=2&late=-5&early=abc");
    expect(s).toMatchObject({ from: "2026-09-01", dow: "all", time_band: "all", service: "all", hour: null, dir: null, late: null, early: null });
  });

  it("leaves compare to the screen that owns it", () => {
    expect(parse("compare=1")).not.toHaveProperty("compare");
    const next = applyScopePatch(new URLSearchParams("compare=1"), presetScopePatch({ dow: "weekday" }));
    expect(next.get("compare")).toBe("1");
  });
});

describe("scopeToQueryString", () => {
  it("round-trips every param in canonical form and omits defaults", () => {
    const qs = "from=2026-08-20&to=2026-09-18&dow=mon%2Cwed&routes=50%2C51&hour=7-9&stop=S1&dir=1&late=180&early=30";
    expect(scopeToQueryString(parse(qs))).toBe(qs);
    expect(scopeToQueryString(parse(""))).toBe("from=2026-09-01&to=2026-09-30");
  });

  it("writes a single hour without a range", () => {
    expect(new URLSearchParams(scopeToQueryString(parse("hour=8-8"))).get("hour")).toBe("8");
  });
});

describe("explicitScopeQuery", () => {
  it("keeps only the scope a URL states, canonical, without filling in the default period", () => {
    expect(explicitScopeQuery("dow=sat%2Csun&report=trend")).toBe("dow=weekend");
    expect(explicitScopeQuery("")).toBe("");
  });

  it("drops time_band for a valid hour only, as scopeToQueryString does", () => {
    expect(explicitScopeQuery("hour=7-9&time_band=evening")).toBe("hour=7-9");
    expect(explicitScopeQuery("hour=99&time_band=evening")).toBe("time_band=evening");
  });
});

describe("applyScopePatch", () => {
  it("preserves params it doesn't know", () => {
    const next = applyScopePatch(new URLSearchParams("report=trend&sub_tab=marey&mode=agencies"), { dow: "sat" });
    expect(next.get("report")).toBe("trend");
    expect(next.get("sub_tab")).toBe("marey");
    expect(next.get("mode")).toBe("agencies");
    expect(next.get("dow")).toBe("sat");
  });

  it("clears on null and on a default value", () => {
    const next = applyScopePatch(new URLSearchParams("dow=sat&stop=S1&routes=1"), { dow: "all", stop: null, routes: [] });
    expect([...next.keys()]).toEqual([]);
  });

  it("keeps hour and time_band exclusive", () => {
    expect(applyScopePatch(new URLSearchParams("time_band=morning"), { hour: [7, 8] }).has("time_band")).toBe(false);
    expect(applyScopePatch(new URLSearchParams("hour=7"), { time_band: "evening" }).has("hour")).toBe(false);
  });

  it("drops an invalid value instead of writing it", () => {
    const next = applyScopePatch(new URLSearchParams(""), { dir: 3 as never, late: 99999, dow: "funday" as never });
    expect([...next.keys()]).toEqual([]);
  });
});

describe("presetScopePatch", () => {
  it("replaces the whole scope, clearing extras a pre-2.1 preset never stored", () => {
    const stored = { from: "2026-08-01", to: "2026-08-31", dow: "weekday", time_band: "morning", service: "all", routes: ["50"] };
    const next = applyScopePatch(new URLSearchParams("hour=7&stop=S1&routes=9&report=trend"), presetScopePatch(stored));
    expect(next.get("time_band")).toBe("morning");
    expect(next.get("routes")).toBe("50");
    expect(next.has("hour")).toBe(false);
    expect(next.has("stop")).toBe(false);
    expect(next.get("report")).toBe("trend");
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

describe("isoDaysBefore", () => {
  it("subtracts calendar days from a given ISO date, not from now", () => {
    expect(isoDaysBefore("2026-06-15", 29)).toBe("2026-05-17");
  });

  it("handles a month boundary", () => {
    expect(isoDaysBefore("2026-03-01", 1)).toBe("2026-02-28");
  });

  it("handles days=0 (returns the same date)", () => {
    expect(isoDaysBefore("2026-06-15", 0)).toBe("2026-06-15");
  });
});
