import { describe, expect, it } from "vitest";
import { routeHeading } from "./routeDisplayName";

const route = (trip_headsigns: string[]) => ({ trip_headsigns });

describe("routeHeading", () => {
  it("reads origin and destination from a headsign, without its code prefix or line name", () => {
    expect(routeHeading(route(["A1明の星→青森駅（国道・古川線）"]))).toEqual({ from: "明の星", to: "青森駅" });
  });

  it("reads a bare destination", () => {
    expect(routeHeading(route(["L21戸山団地（中央大橋線）"]))).toEqual({ from: null, to: "戸山団地" });
  });

  it("drops a destination's own 行/行き suffix, which the label adds back in its own form", () => {
    expect(routeHeading(route(["古川行"]))).toEqual({ from: null, to: "古川" });
    expect(routeHeading(route(["新田行き"]))).toEqual({ from: null, to: "新田" });
  });

  it("leaves a Latin headsign as it is", () => {
    expect(routeHeading(route(["Downtown via Main St"]))).toEqual({ from: null, to: "Downtown via Main St" });
  });

  it("has nothing to say when a route has no headsign, or several to choose between", () => {
    expect(routeHeading(route([]))).toBeNull();
    expect(routeHeading(route(["新田行", "古川行"]))).toBeNull();
    expect(routeHeading(route(["（国道・古川線）"]))).toBeNull();
  });
});
