import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, afterEach } from "vitest";
import { DELAY_RAMP, delayColor, delayColorResolved, severeColorResolved, severityStepColors } from "./tokens";

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

  it("delayColor(>=5) returns the literal var(--delay-severe) string", () => {
    expect(delayColor(15)).toBe("var(--delay-severe)");
    document.documentElement.style.setProperty("--delay-severe", "#A83A1A");
    expect(delayColor(15)).toBe("var(--delay-severe)");
  });

  it("delayColor(<5) returns plain ramp hex, unchanged", () => {
    expect(delayColor(0)).toBe("#2EA87A");
    expect(delayColor(-3)).toBe("#2EA87A");
    expect(delayColor(2)).toBe("#C99A2E");
    expect(delayColor(4)).toBe("#D4622A");
  });

  it("ok/mild/moderate are plain constants", () => {
    expect(DELAY_RAMP.ok).toBe("#2EA87A");
    expect(DELAY_RAMP.mild).toBe("#C99A2E");
    expect(DELAY_RAMP.moderate).toBe("#D4622A");
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
    document.documentElement.style.setProperty("--delay-severe", "#A83A1A");
    expect(severeColorResolved()).toBe("#A83A1A");
  });
});

describe("delayColorResolved() (MapLibre-safe delayColor)", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
    delete document.documentElement.dataset.theme;
  });

  it("matches delayColor() below the severe threshold", () => {
    expect(delayColorResolved(0)).toBe(delayColor(0));
    expect(delayColorResolved(2)).toBe(delayColor(2));
    expect(delayColorResolved(4)).toBe(delayColor(4));
  });

  it("returns a real parseable hex (never the literal var() string) at/above the severe threshold", () => {
    expect(delayColorResolved(15)).toBe("#d92121");
    document.documentElement.style.setProperty("--delay-severe", "#A83A1A");
    expect(delayColorResolved(15)).toBe("#A83A1A");
  });
});

describe("severityStepColors() (MapLibre step-expression stops)", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--delay-severe");
  });

  it("returns the ok/mild/moderate colors at the new 1.5/3/5 thresholds", () => {
    expect(severityStepColors()).toEqual([
      "#2EA87A", 1.5,
      "#C99A2E", 3,
      "#D4622A", 5,
      "#d92121", // severeColorResolved() jsdom fallback
    ]);
  });
});

// ---------------------------------------------------------------------------
// Design-token contract (global.css)
//
// These tokens are a shared vocabulary other stylesheets and components name
// directly, so their presence and exact values are asserted against the
// stylesheet source rather than a computed cascade (jsdom applies no author
// CSS). Reading the file is the point: it is the single source of truth.
// ---------------------------------------------------------------------------
// `process.cwd()` is the `frontend/` package root under vitest; the module
// URL is not a file: URL after vite's transform, so it cannot be used here.
const globalCss = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8");

/** Body of the first rule whose selector text starts at `selector`, with
 *  braces balanced so nested at-rules/rules are included. */
function ruleBody(css: string, selector: string): string {
  const at = css.indexOf(selector);
  if (at === -1) throw new Error(`selector not found: ${selector}`);
  const open = css.indexOf("{", at + selector.length - 1);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  throw new Error(`unbalanced braces after: ${selector}`);
}

/** Last declared value of `prop` in `body` (later declaration wins, matching
 *  the cascade), with runs of whitespace collapsed. */
function decl(body: string, prop: string): string | null {
  const re = new RegExp(`(?:^|[;{\\s])${prop}\\s*:\\s*([^;]+);`, "g");
  let last: string | null = null;
  for (const m of body.matchAll(re)) last = m[1].replace(/\s+/g, " ").trim();
  return last;
}

const rootBlock = ruleBody(globalCss, ":root {");
const darkBlock = ruleBody(globalCss, ':root[data-theme="dark"] {');
const reduceBlock = ruleBody(globalCss, "@media (prefers-reduced-motion: reduce)");

describe("motion tokens", () => {
  it.each([
    ["--dur-1", "150ms"],
    ["--dur-2", "240ms"],
    ["--dur-3", "600ms"],
    ["--dur-4", "1200ms"],
    ["--ease-out", "cubic-bezier(.22, 1, .36, 1)"],
    ["--ease-in-out", "cubic-bezier(.65, 0, .35, 1)"],
  ])("%s is defined on the bare :root as %s", (prop, value) => {
    expect(decl(rootBlock, prop)).toBe(value);
  });

  it("--transition is composed from the motion tokens, not a literal", () => {
    expect(decl(rootBlock, "--transition")).toBe("var(--dur-1) var(--ease-out)");
  });
});

describe("elevation tokens", () => {
  it.each(["--el-1", "--el-2", "--el-3"])("%s is defined in both themes", (prop) => {
    expect(decl(rootBlock, prop)).toBeTruthy();
    expect(decl(darkBlock, prop)).toBeTruthy();
  });

  it("the dark elevations are the hairline/inset treatment, not the light drop shadows", () => {
    for (const prop of ["--el-1", "--el-2", "--el-3"]) {
      const dark = decl(darkBlock, prop)!;
      expect(dark).toContain("inset");
      expect(dark).not.toBe(decl(rootBlock, prop));
    }
    expect(decl(darkBlock, "--el-3")).toMatch(/rgba\(0, ?0, ?0, ?0?\.[5-9]\d*\)/);
  });
});

describe("type scale tokens", () => {
  it.each([
    ["--text-xs", "12px"],
    ["--text-sm", "13px"],
    ["--text-base", "15px"],
    ["--text-md", "17px"],
    ["--text-lg", "20px"],
    ["--text-xl", "26px"],
    ["--text-2xl", "34px"],
    ["--text-3xl", "46px"],
  ])("%s is %s", (prop, value) => {
    expect(decl(rootBlock, prop)).toBe(value);
  });
});

describe("radius tokens", () => {
  it.each([
    ["--radius", "6px"],
    ["--radius-lg", "10px"],
    ["--radius-xl", "14px"],
  ])("%s is %s", (prop, value) => {
    expect(decl(rootBlock, prop)).toBe(value);
  });
});

describe("global reduced-motion regime", () => {
  it("collapses every duration token", () => {
    for (const prop of ["--dur-1", "--dur-2", "--dur-3", "--dur-4"]) {
      expect(decl(reduceBlock, prop)).toBe("0ms");
    }
    expect(decl(reduceBlock, "--transition")).toBe("0s");
  });

  it("neutralises animations and transitions app-wide", () => {
    expect(reduceBlock).toMatch(/\*,\s*\*::before,\s*\*::after/);
    expect(decl(reduceBlock, "animation-duration")).toBe(".01ms !important");
    expect(decl(reduceBlock, "animation-iteration-count")).toBe("1 !important");
    expect(decl(reduceBlock, "transition-duration")).toBe(".01ms !important");
  });
});
