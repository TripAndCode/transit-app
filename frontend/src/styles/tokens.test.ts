import { describe, it, expect, afterEach } from "vitest";
import { DELAY_RAMP, delayColor, severeColorResolved } from "./tokens";

// Two distinct severe-color surfaces:
//  - `DELAY_RAMP.severe` / `delayColor(>10)` return the LITERAL string
//    "var(--delay-severe)" — for DOM/React consumers that render it into an
//    inline `style` prop, where the browser cascade resolves it to the active
//    theme's color for free (recolors on toggle, no re-render needed).
//  - `severeColorResolved()` returns a REAL parseable hex (getComputedStyle of
//    --delay-severe, or the light-mode fallback under jsdom) — for MapLibre
//    call sites, which build plain-JS paint expressions that cannot consume
//    var().
describe("DELAY_RAMP.severe (literal var() for DOM consumers)", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("is the literal var(--delay-severe) string, regardless of theme", () => {
    expect(DELAY_RAMP.severe).toBe("var(--delay-severe)");
    // Even when the custom property is resolvable, the DOM-facing value stays
    // literal — the cascade does the resolving, not JS.
    document.documentElement.style.setProperty("--delay-severe", "#F04438");
    expect(DELAY_RAMP.severe).toBe("var(--delay-severe)");
  });

  it("delayColor(>10) returns the literal var(--delay-severe) string", () => {
    expect(delayColor(15)).toBe("var(--delay-severe)");
    document.documentElement.style.setProperty("--delay-severe", "#F04438");
    expect(delayColor(15)).toBe("var(--delay-severe)");
  });

  it("delayColor(<=10) returns plain ramp hex, unchanged", () => {
    expect(delayColor(0)).toBe("#8fb88f");
    expect(delayColor(-3)).toBe("#8fb88f");
    expect(delayColor(3)).toBe("#d4b878");
    expect(delayColor(7)).toBe("#e07a3a");
  });

  it("ok/mild/moderate are plain constants", () => {
    expect(DELAY_RAMP.ok).toBe("#8fb88f");
    expect(DELAY_RAMP.mild).toBe("#d4b878");
    expect(DELAY_RAMP.moderate).toBe("#e07a3a");
  });
});

describe("severeColorResolved() (real hex for MapLibre)", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("falls back to the light-mode red when --delay-severe is unresolved (jsdom default)", () => {
    expect(severeColorResolved()).toBe("#d92121");
  });

  it("reads --delay-severe when it is set (the dark-mode value in a real cascade)", () => {
    document.documentElement.style.setProperty("--delay-severe", "#F04438");
    expect(severeColorResolved()).toBe("#F04438");
  });
});
