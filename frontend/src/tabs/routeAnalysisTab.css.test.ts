// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./routeAnalysisTab.css"), "utf-8");

describe("routeAnalysisTab.css header sheen", () => {
  it("plays once, timed from the shared motion tokens", () => {
    expect(decl(ruleBody(css, ".route-header-sheen {"), "animation")).toBe(
      "route-header-sheen var(--dur-4) var(--ease-in-out) 1 forwards",
    );
  });

  it("is fully suppressed under reduced motion, not just sped up", () => {
    const reduced = ruleBody(css, "@media (prefers-reduced-motion: reduce) {");
    expect(decl(ruleBody(reduced, ".route-header-sheen {"), "animation")).toBe("none");
  });
});
