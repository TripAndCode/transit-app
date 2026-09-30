import { describe, it, expect } from "vitest";
import { SIDEBAR_NAV_ITEMS } from "./sidebarNavItems";
import { GO_TO_TARGETS } from "./paletteNavTargets";

describe("GO_TO_TARGETS", () => {
  it("has a palette target, with a unique chord key, for every sidebar nav item", () => {
    for (const navItem of SIDEBAR_NAV_ITEMS) {
      const target = GO_TO_TARGETS.find((t) => t.to === navItem.to);
      expect(target, `missing palette target for sidebar nav item "${navItem.to}"`).toBeDefined();
      expect(target!.labelKey).toBe(navItem.labelKey);
    }
  });

  it("assigns a distinct single-letter chord to every target", () => {
    const chordKeys = GO_TO_TARGETS.map((t) => t.chordKey);
    expect(new Set(chordKeys).size).toBe(chordKeys.length);
    for (const key of chordKeys) {
      expect(key).toMatch(/^[a-z]$/);
    }
  });

  it("lists the destinations in rail order, then Ask", () => {
    expect(GO_TO_TARGETS.map((g) => g.to)).toEqual(["pulse", "routes", "time", "why", "compare", "live", "reports", "ask"]);
  });

  it("gives every target a sub-label", () => {
    for (const target of GO_TO_TARGETS) expect(target.sublabelKey, target.to).toBeTruthy();
  });

  it("also includes Ask, which lives in the top bar rather than the rail", () => {
    expect(GO_TO_TARGETS.some((t) => t.to === "ask")).toBe(true);
  });
});
