import { describe, it, expect, afterEach } from "vitest";
import { DELAY_RAMP, delayColor } from "./tokens";

// `DELAY_RAMP.severe` reads the `--delay-severe` CSS custom property (light
// #d92121 / dark #F04438, defined in global.css). In a real browser the value
// tracks the active theme via the cascade. Under jsdom the cascade from
// global.css is NOT applied (vitest runs with `css: false` and global.css is
// never <link>-loaded into the test DOM), so getComputedStyle returns "" and
// the getter falls back to the light-mode default — unless a test sets the
// custom property inline (which jsdom DOES resolve), simulating the cascade.
describe("DELAY_RAMP.severe (CSS custom property, theme-aware)", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("falls back to the light-mode red when --delay-severe is unresolved (default)", () => {
    expect(DELAY_RAMP.severe).toBe("#d92121");
  });

  it("reads --delay-severe when it is set (the dark-mode value in a real cascade)", () => {
    document.documentElement.style.setProperty("--delay-severe", "#F04438");
    expect(DELAY_RAMP.severe).toBe("#F04438");
  });

  it("delayColor(>10) reflects the --delay-severe value", () => {
    expect(delayColor(15)).toBe("#d92121");
    document.documentElement.style.setProperty("--delay-severe", "#F04438");
    expect(delayColor(15)).toBe("#F04438");
  });

  it("ok/mild/moderate are plain constants, unaffected by --delay-severe", () => {
    document.documentElement.style.setProperty("--delay-severe", "#F04438");
    expect(DELAY_RAMP.ok).toBe("#8fb88f");
    expect(DELAY_RAMP.mild).toBe("#d4b878");
    expect(DELAY_RAMP.moderate).toBe("#e07a3a");
  });
});
