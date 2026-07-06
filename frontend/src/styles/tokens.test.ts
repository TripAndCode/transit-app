import { describe, it, expect, afterEach } from "vitest";
import { DELAY_RAMP, delayColor } from "./tokens";

describe("DELAY_RAMP.severe (theme-aware)", () => {
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it("returns the light-mode red when no theme is set", () => {
    expect(DELAY_RAMP.severe).toBe("#d92121");
  });

  it("returns a brighter red in dark mode (light-mode red fails contrast on dark backgrounds)", () => {
    document.documentElement.dataset.theme = "dark";
    expect(DELAY_RAMP.severe).toBe("#F04438");
  });

  it("delayColor(>10) reflects the same theme-aware severe value", () => {
    expect(delayColor(15)).toBe("#d92121");
    document.documentElement.dataset.theme = "dark";
    expect(delayColor(15)).toBe("#F04438");
  });

  it("ok/mild/moderate are unaffected by theme", () => {
    document.documentElement.dataset.theme = "dark";
    expect(DELAY_RAMP.ok).toBe("#8fb88f");
    expect(DELAY_RAMP.mild).toBe("#d4b878");
    expect(DELAY_RAMP.moderate).toBe("#e07a3a");
  });
});
