// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./focusedAnalysis.css"), "utf-8");

describe("focusedAnalysis.css Marey scrubber", () => {
  it("hover keeps the quick tier; only a set scrub slows trips to the panel tier", () => {
    expect(decl(ruleBody(css, ".marey-trip {"), "transition")).toBe("opacity var(--dur-1) var(--ease-out)");
    expect(decl(ruleBody(css, ".marey--scrubbing .marey-trip {"), "transition")).toBe("opacity var(--dur-2) var(--ease-out)");
  });
  it("a ribbon marker travels by transform, never by its geometry", () => {
    expect(decl(ruleBody(css, ".stop-ribbon__pos {"), "transition")).toBe("transform var(--dur-2) var(--ease-out)");
  });

  it("draws the rail's track, which the shared scrubber chrome leaves to its caller", () => {
    for (const track of [".marey-scrub .scrub-input::-webkit-slider-runnable-track {", ".marey-scrub .scrub-input::-moz-range-track {"]) {
      expect(decl(ruleBody(css, track), "background")).toBe("var(--track-bg)");
    }
  });
});
