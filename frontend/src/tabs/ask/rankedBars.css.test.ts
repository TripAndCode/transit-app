import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./rankedBars.css"), "utf8");
const MOTION_ALLOWED = "@media (prefers-reduced-motion: no-preference)";
const MOTION_REDUCED = "@media (prefers-reduced-motion: reduce)";

describe("rankedBars.css motion", () => {
  it("rests at the bar's own finished share, not scaleX(0)", () => {
    expect(decl(ruleBody(css, ".ask-rank-bar-fill {"), "transform")).toBe("scaleX(var(--bar-share, 0))");
  });

  it("the grow-in animation lives only inside the motion-allowed block, on token timing", () => {
    const allowed = ruleBody(css.slice(css.indexOf(MOTION_ALLOWED)), MOTION_ALLOWED);
    expect(allowed).toMatch(
      /\.ask-rank-bar-fill\s*\{[^}]*animation:\s*ask-rank-bar-grow var\(--dur-3\) var\(--ease-out\) backwards/,
    );
    // The stagger is a token multiple, never a hand-typed millisecond count.
    expect(decl(allowed, "animation-delay")).toBe("calc(var(--stagger, 0) * var(--dur-1) / 3)");
    const outside = css.replace(allowed, "");
    expect(outside).not.toMatch(/ask-rank-bar-grow/);
  });

  it("declares no animation on .ask-rank-bar-fill outside the motion-allowed block", () => {
    expect(decl(ruleBody(css, ".ask-rank-bar-fill {"), "animation")).toBeNull();
  });

  it("explicitly turns the animation off under reduced motion, leaving the bar at its finished share", () => {
    const reduced = ruleBody(css.slice(css.indexOf(MOTION_REDUCED)), MOTION_REDUCED);
    expect(decl(ruleBody(reduced, ".ask-rank-bar-fill {"), "animation")).toBe("none");
  });
});
