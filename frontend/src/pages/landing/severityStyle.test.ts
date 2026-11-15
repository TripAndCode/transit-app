import { describe, it, expect } from "vitest";
import { fixtureFill } from "./severityStyle";
import { DELAY_RAMP } from "../../styles/tokens";

describe("fixtureFill", () => {
  it("pins the severe fill to the theme-independent rust primitive, not the per-theme delay-severe token", () => {
    expect(fixtureFill("severe")).not.toBe(DELAY_RAMP.severe);
    expect(fixtureFill("severe")).toBe("var(--p-rust-50)");
  });

  it("reuses the shared delay ramp for every other band", () => {
    expect(fixtureFill("ok")).toBe(DELAY_RAMP.ok);
    expect(fixtureFill("mild")).toBe(DELAY_RAMP.mild);
    expect(fixtureFill("moderate")).toBe(DELAY_RAMP.moderate);
  });

  it("gives the offline band its own fill, distinct from the delay ramp", () => {
    expect(fixtureFill("offline")).toBe("#101830");
  });
});
