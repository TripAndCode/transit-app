import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./thinkingShimmer.css"), "utf8");
const MOTION_ALLOWED = "@media (prefers-reduced-motion: no-preference)";
const MOTION_REDUCED = "@media (prefers-reduced-motion: reduce)";

describe("thinkingShimmer.css motion", () => {
  it("the sweep lives only inside the motion-allowed block, on token timing, looping", () => {
    const allowed = ruleBody(css.slice(css.indexOf(MOTION_ALLOWED)), MOTION_ALLOWED);
    expect(allowed).toMatch(/\.ask-thinking-shimmer-line\s*\{[^}]*animation:\s*ask-thinking-sweep var\(--dur-4\) linear infinite/);
    const outside = css.replace(allowed, "");
    expect(outside).not.toMatch(/ask-thinking-sweep/);
  });

  it("declares no animation on .ask-thinking-shimmer-line outside the motion-allowed block", () => {
    expect(decl(ruleBody(css, ".ask-thinking-shimmer-line {"), "animation")).toBeNull();
  });

  it("explicitly turns the sweep off under reduced motion, so the shimmer goes static", () => {
    const reduced = ruleBody(css.slice(css.indexOf(MOTION_REDUCED)), MOTION_REDUCED);
    expect(decl(ruleBody(reduced, ".ask-thinking-shimmer-line {"), "animation")).toBe("none");
  });
});
