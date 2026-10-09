import { describe, expect, it } from "vitest";
import { SCOPE_EXTRAS_NONE, type Scope } from "./scope";
import { scopeSummaryQuery } from "./scopeSummaryQuery";

const base: Scope = {
  ...SCOPE_EXTRAS_NONE,
  from: "2026-09-01",
  to: "2026-09-28",
  dow: "weekday",
  time_band: "all",
  service: "all",
  routes: ["R1"],
};

describe("scopeSummaryQuery", () => {
  it("keeps only the fields the summary answers to", () => {
    const q = new URLSearchParams(scopeSummaryQuery({ ...base, late: 180, time_band: "morning", stop: "S1", dir: 1, early: 60 }));
    expect(Object.fromEntries(q)).toEqual({ from: "2026-09-01", to: "2026-09-28", dow: "weekday", routes: "R1", early: "60" });
  });

  it("is the same for scopes that differ only in fields it ignores", () => {
    expect(scopeSummaryQuery({ ...base, late: 300 })).toBe(scopeSummaryQuery(base));
    expect(scopeSummaryQuery({ ...base, hour: [7, 9] })).toBe(scopeSummaryQuery(base));
  });
});
