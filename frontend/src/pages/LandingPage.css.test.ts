import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const css = readFileSync(resolve(__dirname, "./LandingPage.css"), "utf-8");

const REDUCED_MOTION_MARKER = "@media (prefers-reduced-motion: no-preference)";

function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Returns exactly the `@media (prefers-reduced-motion: no-preference)` block,
 *  found by brace depth from its opening `{`, so a block that follows it (such
 *  as the later `@media (max-width)` query) is never counted as inside it. */
function findReducedMotionBlock(source: string): { start: number; end: number; body: string } {
  const markerIndex = source.indexOf(REDUCED_MOTION_MARKER);
  if (markerIndex === -1) throw new Error(`no "${REDUCED_MOTION_MARKER}" block found`);
  const openBrace = source.indexOf("{", markerIndex);
  if (openBrace === -1) throw new Error(`"${REDUCED_MOTION_MARKER}" has no opening brace`);
  let depth = 1;
  let i = openBrace + 1;
  while (depth > 0) {
    if (i >= source.length) throw new Error(`"${REDUCED_MOTION_MARKER}" braces never balance`);
    if (source[i] === "{") depth++;
    else if (source[i] === "}") depth--;
    i++;
  }
  return { start: markerIndex, end: i, body: source.slice(openBrace + 1, i - 1) };
}

const strippedCss = stripComments(css);

function reducedMotionBody(): string {
  return findReducedMotionBlock(strippedCss).body;
}

function outsideReducedMotion(): string {
  const { start, end } = findReducedMotionBlock(strippedCss);
  return strippedCss.slice(0, start) + strippedCss.slice(end);
}

describe("LandingPage.css motion", () => {
  it("declares every @keyframes block only inside the reduced-motion-allowed media query", () => {
    const names = [...strippedCss.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    const insideNames = [...reducedMotionBody().matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    expect(insideNames.sort()).toEqual(names.sort());
    expect(outsideReducedMotion()).not.toMatch(/@keyframes/);
  });

  it("every animation-name/animation shorthand referencing a keyframe lives inside that same media query", () => {
    const names = [...strippedCss.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    const outside = outsideReducedMotion();
    for (const name of names) {
      expect(outside).not.toMatch(new RegExp(`animation(?:-name)?\\s*:[^;]*\\b${name}\\b`));
    }
  });

  it("isolates the reduced-motion block from the phone-width block that follows it", () => {
    expect(strippedCss).toMatch(/\.landing-header__cta\s*\{\s*display:\s*none/);
    expect(reducedMotionBody()).not.toContain(".landing-header__cta");
  });

  it("the pause toggle's freeze rule works whether or not motion is already reduced, so it stays outside the media query", () => {
    expect(outsideReducedMotion()).toMatch(/\.landing-shell\.is-paused[^{]*\{[^}]*animation-play-state:\s*paused/);
  });
});

describe("LandingPage.css tokens", () => {
  it("never hardcodes the delay-severity ramp's hex values directly; it reuses the --delay-ok..--delay-severe tokens", () => {
    for (const hex of ["#2EA87A", "#C99A2E", "#D4622A", "#A8391F"]) {
      expect(css).not.toContain(hex);
      expect(css.toLowerCase()).not.toContain(`%23${hex.slice(1).toLowerCase()}`);
    }
  });
});
