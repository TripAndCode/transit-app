import { describe, it, expect } from "vitest";
import { busTownHref } from "./busTownLink";

describe("busTownHref", () => {
  it("sends a visitor who reaches the app to their last agency's Bus Town", () => {
    expect(busTownHref(true, 7)).toBe("/agencies/7/bus-town");
  });

  it("sends a visitor who reaches the app but has no last agency to the app root", () => {
    expect(busTownHref(true, null)).toBe("/");
  });

  it("sends a visitor who would not reach the app to sign-in, regardless of a stored agency", () => {
    expect(busTownHref(false, 7)).toBe("/login");
    expect(busTownHref(false, null)).toBe("/login");
  });
});
