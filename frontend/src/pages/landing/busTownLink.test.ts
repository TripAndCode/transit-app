import { describe, it, expect } from "vitest";
import { busTownHref } from "./busTownLink";

describe("busTownHref", () => {
  it("sends a visitor to their last agency's Bus Town", () => {
    expect(busTownHref(7)).toBe("/agencies/7/bus-town");
  });

  it("sends a visitor with no last agency to the app root", () => {
    expect(busTownHref(null)).toBe("/");
  });
});
