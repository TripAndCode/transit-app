import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, afterEach } from "vitest";
import {
  DELAY_RAMP,
  contrastRatio,
  delayColor,
  delayColorResolved,
  readableInkOn,
  severeColorResolved,
  severityStepColors,
  surfaceColorResolved,
} from "./tokens";

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
    document.documentElement.style.setProperty("--delay-severe", "#F0837A");
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
    expect(severeColorResolved()).toBe("#A8391F");
  });

  it("reads --delay-severe when it is set (the dark-mode value in a real cascade)", () => {
    document.documentElement.style.setProperty("--delay-severe", "#F0837A");
    expect(severeColorResolved()).toBe("#F0837A");
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
    expect(delayColorResolved(15)).toBe("#A8391F");
    document.documentElement.style.setProperty("--delay-severe", "#F0837A");
    expect(delayColorResolved(15)).toBe("#F0837A");
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
      "#A8391F", // severeColorResolved() jsdom fallback
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
// Comments are stripped before parsing: prose explaining a token routinely
// contains a `--token: value`-shaped phrase, and `decl()` would otherwise read
// the sentence instead of the declaration.
const globalCss = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

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
const bodyBlock = ruleBody(globalCss, "body {");

const overviewCss = readFileSync(resolve(process.cwd(), "src/styles/overview.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);
const ovPageBlock = ruleBody(overviewCss, ".ov-page {");
const ovKpiValueBlock = ruleBody(overviewCss, ".ov-kpi-value {");

const indexHtml = readFileSync(resolve(process.cwd(), "index.html"), "utf8");

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

describe("Japanese body typography", () => {
  it("uses proportional (palt) spacing, strict line breaking, and safe wrapping", () => {
    expect(decl(bodyBlock, "font-feature-settings")).toBe('"palt" 1');
    expect(decl(bodyBlock, "line-break")).toBe("strict");
    expect(decl(bodyBlock, "overflow-wrap")).toBe("anywhere");
  });

  it("declares text-spacing-trim as a progressive enhancement", () => {
    expect(decl(bodyBlock, "text-spacing-trim")).toBe("space-first");
  });
});

describe(".num — the single place tabular figures are turned on", () => {
  it("applies tabular-nums", () => {
    const numBlock = ruleBody(globalCss, ".num {");
    expect(decl(numBlock, "font-variant-numeric")).toBe("tabular-nums");
  });
});

describe("--font-display policy", () => {
  it("is documented as an identity-moments-only font next to its declaration", () => {
    const rawGlobalCss = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8");
    const idx = rawGlobalCss.indexOf("--font-display:");
    const preceding = rawGlobalCss.slice(Math.max(0, idx - 400), idx);
    expect(preceding).toMatch(/brand wordmark/);
    expect(preceding).toMatch(/welcome headline/i);
  });
});

describe("period overview uses shared typography, not a page-local override", () => {
  it("does not override font-family — inherits --font-body like every other tab", () => {
    expect(decl(ovPageBlock, "font-family")).toBeNull();
  });

  it("does not blanket the whole page in tabular figures", () => {
    expect(decl(ovPageBlock, "font-feature-settings")).toBeNull();
  });
});

describe("hero KPI values use proportional figures, not tabular-nums", () => {
  it("does not force tabular-nums on the large standalone hero number", () => {
    expect(decl(ovKpiValueBlock, "font-variant-numeric")).toBeNull();
  });
});

describe("index.html Noto font loading", () => {
  it("preloads the Google Fonts stylesheet", () => {
    expect(indexHtml).toMatch(
      /<link rel="preload" as="style" href="https:\/\/fonts\.googleapis\.com\/css2\?family=Noto\+Sans\+JP[^"]*">/,
    );
  });

  it("keeps the gstatic preconnect", () => {
    expect(indexHtml).toMatch(/<link rel="preconnect" href="https:\/\/fonts\.gstatic\.com" crossorigin>/);
  });

  it("requests only the font weights actually used in the app", () => {
    expect(indexHtml).toMatch(/Noto\+Sans\+JP:wght@400;500;600;700;800/);
    expect(indexHtml).toMatch(/Noto\+Serif\+JP:wght@400;600/);
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

// ---------------------------------------------------------------------------
// Colour identity
// ---------------------------------------------------------------------------

/** Channel triple of a `#rrggbb` token value. */
function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

describe("one accent identity", () => {
  it.each([
    [rootBlock, "--accent", "#187b80"],
    [rootBlock, "--accent-soft", "#e1f1f1"],
    [darkBlock, "--accent", "#43c5ba"],
    [darkBlock, "--accent-soft", "#183b3d"],
  ])("declares the teal accent", (block, prop, value) => {
    expect(decl(block, prop)).toBe(value);
  });

  it("carries no scoped --accent override that would fork the identity", () => {
    // A `.app-shell { --accent: … }` (or any other scoped redefinition) means
    // the signed-in shell and the pre-auth pages render different accents.
    // The token is only allowed to be declared on the two theme roots.
    const accentDeclarations = [...globalCss.matchAll(/([^{}]*)\{[^{}]*--accent\s*:/g)]
      .map((m) => m[1].trim().split(/\s*\n\s*/).pop()!.trim());
    expect(accentDeclarations).toEqual([":root", ':root[data-theme="dark"]']);
  });

  it("keeps the blue-purple as --brand in both themes, not as --accent", () => {
    expect(decl(rootBlock, "--brand")).toBe("#5b6cad");
    expect(decl(darkBlock, "--brand")).toBe("#7E8CD0");
    expect(decl(rootBlock, "--on-brand")).toBeTruthy();
    expect(decl(darkBlock, "--on-brand")).toBeTruthy();
  });

  it("keeps --accent-strong in the accent's own hue family", () => {
    // --accent-strong is documented as a deepened --accent; if it stays in a
    // different hue family the app still reads as two brands.
    for (const block of [rootBlock, darkBlock]) {
      const [ar, ag, ab] = rgb(decl(block, "--accent")!);
      const [sr, sg, sb] = rgb(decl(block, "--accent-strong")!);
      // Teal: green and blue both dominate red, in the accent and its
      // deepened sibling alike.
      expect(Math.min(ag, ab)).toBeGreaterThan(ar);
      expect(Math.min(sg, sb)).toBeGreaterThan(sr);
    }
  });

  it("keeps --accent and --accent-strong readable at text weight", () => {
    for (const block of [rootBlock, darkBlock]) {
      for (const prop of ["--accent", "--accent-strong"]) {
        for (const surface of ["--bg-surface", "--bg-soft"]) {
          expect(contrastRatio(decl(block, prop)!, decl(block, surface)!)).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });
});

describe("--delay-severe clears AA on its own theme's surface", () => {
  const lightSevere = decl(rootBlock, "--delay-severe")!;
  const darkSevere = decl(darkBlock, "--delay-severe")!;
  const lightSurface = decl(rootBlock, "--bg-surface")!;
  const darkSurface = decl(darkBlock, "--bg-surface")!;

  it("the light value clears 4.5:1 on the light surface", () => {
    expect(contrastRatio(lightSevere, lightSurface)).toBeGreaterThanOrEqual(4.5);
  });

  it("the dark value clears 4.5:1 on the dark surface", () => {
    expect(contrastRatio(darkSevere, darkSurface)).toBeGreaterThanOrEqual(4.5);
  });

  it("neither value clears AA on the OTHER theme's surface", () => {
    // This is the invariant that forces the token to be per-theme: a single
    // hex dark enough for white is too dark for a near-black page, and vice
    // versa. If both of these ever pass, the split is no longer necessary.
    expect(contrastRatio(lightSevere, darkSurface)).toBeLessThan(4.5);
    expect(contrastRatio(darkSevere, lightSurface)).toBeLessThan(4.5);
  });

  it("stays a deep warm tone, not a saturated alarm red", () => {
    // Pure/near-pure red (a fully saturated hue-0 channel) is the alarm
    // signal the calm-UI rule rules out; the severe tier is the deep end of
    // the warm ramp instead, so it keeps a visible green and blue component.
    for (const hex of [lightSevere, darkSevere]) {
      const [r, g, b] = rgb(hex);
      expect(g).toBeGreaterThan(0x20);
      expect(b).toBeGreaterThan(0x18);
      expect(r - g).toBeLessThan(0x90);
    }
  });

  it("SEVERE_FALLBACK (via severeColorResolved under jsdom) tracks the light CSS value", () => {
    // There is no build-time link between tokens.ts and global.css, so this
    // assertion is what keeps the hand-mirrored pair from drifting.
    expect(severeColorResolved()).toBe(lightSevere);
  });
});

describe("contrastRatio()", () => {
  it("is 21:1 for black on white and 1:1 for a colour on itself", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrastRatio("#187b80", "#187b80")).toBeCloseTo(1, 5);
  });

  it("is symmetric in its arguments", () => {
    expect(contrastRatio("#A8391F", "#ffffff")).toBeCloseTo(contrastRatio("#ffffff", "#A8391F"), 10);
  });
});

describe("readableInkOn()", () => {
  it("picks whichever ink clears AA on every colour in the delay ramp", () => {
    const ramp = [DELAY_RAMP.ok, DELAY_RAMP.mild, DELAY_RAMP.moderate, "#A8391F", "#F0837A"];
    for (const bg of ramp) {
      expect(contrastRatio(readableInkOn(bg), bg)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("goes light on the deep severe red and dark on the pale one", () => {
    expect(readableInkOn("#A8391F")).toBe("#ffffff");
    expect(readableInkOn("#F0837A")).not.toBe("#ffffff");
  });
});

describe("surfaceColorResolved()", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--bg-surface");
  });

  it("falls back to the light surface when --bg-surface is unresolved (jsdom)", () => {
    expect(surfaceColorResolved()).toBe("#ffffff");
  });

  it("reads --bg-surface when the cascade provides it", () => {
    document.documentElement.style.setProperty("--bg-surface", "#141726");
    expect(surfaceColorResolved()).toBe("#141726");
  });
});

describe("route-enter animation", () => {
  it("is declared once, inside a motion-allowed block, reusing the shared fade keyframes", () => {
    // Nothing outside a no-preference block may define it: the class is
    // applied unconditionally by RouteTransition, so the media query is the
    // only thing standing between it and a reduced-motion user.
    const allowed = [...globalCss.matchAll(/@media \(prefers-reduced-motion: no-preference\)/g)]
      .map((m) => ruleBody(globalCss.slice(m.index), "@media (prefers-reduced-motion: no-preference)"))
      .filter((block) => block.includes(".route-enter"));

    expect(globalCss.match(/\.route-enter/g)).toHaveLength(1);
    expect(allowed).toHaveLength(1);
    expect(decl(ruleBody(allowed[0], ".route-enter"), "animation")).toBe(
      "ov-fade-in var(--dur-2) var(--ease-out)",
    );
  });
});

describe("chart entrance motion (ChartEnter.tsx)", () => {
  function motionAllowedBlocksContaining(selector: string): string[] {
    return [...globalCss.matchAll(/@media \(prefers-reduced-motion: no-preference\)/g)]
      .map((m) => ruleBody(globalCss.slice(m.index), "@media (prefers-reduced-motion: no-preference)"))
      .filter((block) => block.includes(selector));
  }

  it("draws a line on via --len, only inside a motion-allowed block", () => {
    const allowed = motionAllowedBlocksContaining(".chart-draw-on {");
    expect(globalCss.match(/\.chart-draw-on \{/g)).toHaveLength(1);
    expect(allowed).toHaveLength(1);
    const body = ruleBody(allowed[0], ".chart-draw-on {");
    expect(decl(body, "stroke-dasharray")).toBe("var(--len, 9999)");
    expect(decl(body, "stroke-dashoffset")).toBe("var(--len, 9999)");
    expect(decl(body, "transition")).toBe("stroke-dashoffset var(--dur-4) var(--ease-out)");
    expect(decl(ruleBody(allowed[0], ".chart-draw-on.chart-draw-on--active {"), "stroke-dashoffset")).toBe("0");
  });

  it("fades a staggered cell in via opacity, only inside a motion-allowed block", () => {
    const allowed = motionAllowedBlocksContaining(".chart-cell-enter {");
    expect(globalCss.match(/\.chart-cell-enter \{/g)).toHaveLength(1);
    expect(allowed).toHaveLength(1);
    const body = ruleBody(allowed[0], ".chart-cell-enter {");
    expect(decl(body, "opacity")).toBe("0");
    expect(decl(body, "transition")).toBe("opacity var(--dur-3) var(--ease-out)");
    // The target opacity is per-cell, not a flat 1 -- HourlyHeatmap's cells
    // encode sample density as opacity, and the fade-in must land on that
    // value rather than overriding it.
    expect(decl(ruleBody(allowed[0], ".chart-cell-enter.chart-cell-enter--in {"), "opacity")).toBe(
      "var(--cell-opacity, 1)",
    );
  });
});

describe("progressive reveal (RevealSection.tsx's useInView())", () => {
  function motionAllowedBlocksContaining(selector: string): string[] {
    return [...globalCss.matchAll(/@media \(prefers-reduced-motion: no-preference\)/g)]
      .map((m) => ruleBody(globalCss.slice(m.index), "@media (prefers-reduced-motion: no-preference)"))
      .filter((block) => block.includes(selector));
  }

  it("is transform-only and visible at rest -- never opacity: 0 -- only inside a motion-allowed block", () => {
    const allowed = motionAllowedBlocksContaining(".reveal {");
    expect(globalCss.match(/\.reveal \{/g)).toHaveLength(1);
    expect(allowed).toHaveLength(1);

    const body = ruleBody(allowed[0], ".reveal {");
    expect(decl(body, "opacity")).toBeNull();
    expect(decl(body, "transform")).toBe("translateY(16px)");
    expect(decl(body, "transition")).toBe("transform var(--dur-3) var(--ease-out)");

    const inBody = ruleBody(allowed[0], ".reveal.reveal--in {");
    expect(decl(inBody, "opacity")).toBeNull();
    expect(decl(inBody, "transform")).toBe("translateY(0)");
  });
});

describe("tooltip surface", () => {
  it("no longer ships the CSS-only .tip pseudo-element tooltip", () => {
    expect(globalCss).not.toContain("content: attr(data-tip)");
    expect(globalCss).not.toContain(".tip--below");
  });

  it("paints the Tooltip primitive from the tooltip and elevation tokens", () => {
    const tooltipBlock = ruleBody(globalCss, ".tooltip {");
    expect(decl(tooltipBlock, "background")).toBe("var(--tooltip-bg)");
    expect(decl(tooltipBlock, "color")).toBe("var(--tooltip-fg)");
    expect(decl(tooltipBlock, "box-shadow")).toBe("var(--el-2)");
    expect(decl(tooltipBlock, "pointer-events")).toBe("none");
    expect(decl(tooltipBlock, "position")).toBe("fixed");
  });
});

// The tab bar's height is a number in Sidebar.tsx (it positions the More
// sheet above the bar) and a length in global.css (it reserves the same
// space under the routed content). Edited in one place only, the bar either
// overlaps the content or leaves a gap, and nothing else notices.
describe("mobile tab bar height", () => {
  it("is the same value in Sidebar.tsx and global.css", () => {
    const ts = readFileSync(resolve(process.cwd(), "src/components/Sidebar.tsx"), "utf8");
    const fromTs = ts.match(/const MOBILE_TABBAR_HEIGHT_PX = (\d+);/)?.[1];
    expect(fromTs, "MOBILE_TABBAR_HEIGHT_PX not found in Sidebar.tsx").toBeDefined();

    const css = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8");
    const fromCss = css.match(/\.app-main \{ padding-bottom: calc\((\d+)px \+ env\(safe-area-inset-bottom\)\)/)?.[1];
    expect(fromCss, "the .app-main mobile padding rule was not found").toBeDefined();

    expect(fromCss).toBe(fromTs);
  });
});

// The sheet's peek height drives its own inline height from TS, while the
// map's playback transport has to clear that height from CSS. Nothing links
// them at runtime, so raising one alone silently hides the transport behind
// the sheet at its resting height.
describe("bottom sheet peek height", () => {
  it("is the same value in bottomSheetSnap.ts and operationsMap.css", () => {
    const ts = readFileSync(resolve(process.cwd(), "src/components/bottomSheetSnap.ts"), "utf8");
    const fromTs = ts.match(/SNAP_HEIGHT_VH[^=]*=\s*\{\s*peek:\s*(\d+)/)?.[1];
    expect(fromTs, "SNAP_HEIGHT_VH.peek not found in bottomSheetSnap.ts").toBeDefined();

    const css = readFileSync(resolve(process.cwd(), "src/tabs/map/operationsMap.css"), "utf8");
    const fromCss = css.match(/\.ops-playback \{ bottom: calc\((\d+)vh \+ \d+px\); \}/)?.[1];
    expect(fromCss, "the .ops-playback mobile offset rule was not found").toBeDefined();

    expect(fromCss).toBe(fromTs);
  });
});
