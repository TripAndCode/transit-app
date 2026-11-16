import { describe, expect, it } from "vitest";
import { SCOPE_EXTRAS_NONE, type Scope } from "../../api/scope";
import { applyLiveScopePatch, liveShapeScope } from "./liveScope";

const SELECTED = "routes=R1&route_focus=R1&route_focus_agency=1&direction=0&trip=T1&style=dark";

describe("applyLiveScopePatch", () => {
  it("drops the map's selection along with a change to the routes", () => {
    const next = applyLiveScopePatch(new URLSearchParams(SELECTED), { routes: ["R2"] });
    expect(next.toString()).toBe("routes=R2&style=dark");
  });

  it("keeps the selection through any other change", () => {
    const next = applyLiveScopePatch(new URLSearchParams(SELECTED), { late: 120 });
    expect(next.get("late")).toBe("120");
    expect(next.get("trip")).toBe("T1");
    expect(next.get("route_focus")).toBe("R1");
  });

  it("keeps the selection through a reset that leaves the routes as they were", () => {
    const prev = new URLSearchParams("route_focus=R1&route_focus_agency=1&direction=0&trip=T1&dow=weekday");
    const next = applyLiveScopePatch(prev, { routes: null, dow: "all" });
    expect(next.has("dow")).toBe(false);
    expect(next.get("route_focus")).toBe("R1");
    expect(next.get("trip")).toBe("T1");
  });
});

describe("liveShapeScope", () => {
  it("draws a route's shape over the default period, whatever period and days a link carries", () => {
    const linked: Scope = {
      ...SCOPE_EXTRAS_NONE,
      from: "2020-01-01", to: "2020-01-31", dow: "weekday", time_band: "morning", service: "all",
      routes: ["R1"], stop: "S1", late: 120,
    };
    expect(liveShapeScope(linked, "2026-09-30")).toEqual({
      ...SCOPE_EXTRAS_NONE,
      from: "2026-09-01", to: "2026-09-30", dow: "all", time_band: "all", service: "all", routes: ["R1"],
    });
  });
});
