import { describe, expect, it } from "vitest";
import { popoverLeft } from "./popoverPosition";

describe("popoverLeft", () => {
  it("opens under its token when it fits", () => {
    expect(popoverLeft(40, 300, 800)).toBe(40);
  });

  it("slides left so the popover ends inside its container", () => {
    expect(popoverLeft(282, 358, 358)).toBe(0);
    expect(popoverLeft(600, 300, 800)).toBe(500);
  });

  it("never starts left of its container", () => {
    expect(popoverLeft(-10, 300, 800)).toBe(0);
  });
});
