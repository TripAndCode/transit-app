import { describe, expect, it } from "vitest";
import { applyLiveScopePatch } from "./liveScope";

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
});
