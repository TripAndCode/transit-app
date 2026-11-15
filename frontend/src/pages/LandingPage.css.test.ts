import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const css = readFileSync(resolve(__dirname, "./LandingPage.css"), "utf-8");

const REDUCED_MOTION_BLOCK = /@media \(prefers-reduced-motion:\s*no-preference\)\s*\{([\s\S]*)\n\}\n/;

function reducedMotionBody(): string {
  const match = css.match(REDUCED_MOTION_BLOCK);
  if (!match) throw new Error("no @media (prefers-reduced-motion: no-preference) block found");
  return match[1];
}

function outsideReducedMotion(): string {
  return css.replace(REDUCED_MOTION_BLOCK, "");
}

describe("LandingPage.css motion", () => {
  it("declares every @keyframes block only inside the reduced-motion-allowed media query", () => {
    const names = [...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    expect(names.length).toBeGreaterThan(0);
    const insideNames = [...reducedMotionBody().matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    expect(insideNames.sort()).toEqual(names.sort());
    expect(outsideReducedMotion()).not.toMatch(/@keyframes/);
  });

  it("every animation-name/animation shorthand referencing a keyframe lives inside that same media query", () => {
    const names = [...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
    const outside = outsideReducedMotion();
    for (const name of names) {
      expect(outside).not.toMatch(new RegExp(`animation(?:-name)?\\s*:[^;]*\\b${name}\\b`));
    }
  });

  it("the pause toggle's freeze rule works whether or not motion is already reduced, so it stays outside the media query", () => {
    expect(outsideReducedMotion()).toMatch(/\.landing-shell\.is-paused[^{]*\{[^}]*animation-play-state:\s*paused/);
  });
});

describe("LandingPage.css tokens", () => {
  it("never hardcodes the delay-severity ramp's hex values directly; it reuses the registered --d1..--d4 tokens", () => {
    for (const hex of ["#2EA87A", "#C99A2E", "#D4622A", "#A8391F"]) {
      expect(css).not.toContain(hex);
    }
  });
});
