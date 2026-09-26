import { describe, it, expect } from "vitest";
import { BP } from "./breakpoints";
import { MOBILE_BREAKPOINT_PX, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";

describe("breakpoints", () => {
  it("exposes the three layout breakpoints", () => {
    expect(BP).toEqual({ sm: 640, md: 900, lg: 1200 });
  });

  it("is the source the mobile-query hook reads, so the two cannot drift", () => {
    expect(MOBILE_BREAKPOINT_PX).toBe(BP.sm);
    expect(MOBILE_BREAKPOINT_QUERY).toBe(`(max-width: ${BP.sm}px)`);
  });
});
