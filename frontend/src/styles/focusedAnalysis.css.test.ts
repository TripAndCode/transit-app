// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./focusedAnalysis.css"), "utf-8");

describe("focusedAnalysis.css Marey scrubber", () => {
  it("draws the rail's track, which the shared scrubber chrome leaves to its caller", () => {
    for (const track of [".marey-scrub .scrub-input::-webkit-slider-runnable-track {", ".marey-scrub .scrub-input::-moz-range-track {"]) {
      expect(decl(ruleBody(css, track), "background")).toBe("var(--track-bg)");
    }
  });
});
