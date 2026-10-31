// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../../test/cssRules";
const css = readFileSync(resolve(process.cwd(), "src/components/charts/compareBars.css"), "utf8");
describe("compareBars.css", () => {
  it("bars morph by transform on --dur-3 and rows travel by transform", () => {
    const bar = ruleBody(css, ".compare-bar__track i {");
    // Slid rather than scaled, so the ghost's dashed edge and the bars'
    // corners keep their drawn size at any share.
    expect(decl(bar, "transform")).toBe("translateX(calc((var(--bar-share, 0) - 1) * 100%))");
    expect(decl(ruleBody(css, ".compare-bar__track {"), "overflow")).toBe("hidden");
    expect(decl(bar, "transition")).toBe("transform var(--dur-3) var(--ease-out), background-color var(--dur-3) var(--ease-out)");
    expect(decl(ruleBody(css, ".compare-bar-row {"), "transition")).toBe("transform var(--dur-3) var(--ease-out), background var(--dur-1) var(--ease-out)");
    expect(css).not.toMatch(/transition:[^;]*\b(width|height|left|top)\b/);
  });
  it("on a narrow screen the bar takes the second row and the gap the third, so the gap is never dropped", () => {
    const narrow = ruleBody(css, "@media (max-width: 640px) {");
    expect(decl(ruleBody(narrow, ".compare-bar__track {"), "grid-row")).toBe("2");
    const gap = ruleBody(narrow, ".compare-bar__gap {");
    expect(decl(gap, "grid-row")).toBe("3");
    expect(decl(gap, "display")).toBeNull();
  });
  it("the gap reads in a neutral ink, with no up/down colour pair", () => {
    expect(decl(ruleBody(css, ".compare-bar__gap {"), "color")).toBe("var(--text-secondary)");
    expect(css).not.toMatch(/--delay-text-(ok|moderate)/);
  });
  it("the ghost is a dashed outline in the border colour", () => {
    expect(decl(ruleBody(css, ".compare-bar__ghost {"), "border")).toBe("1px dashed var(--border-subtle)");
  });
});
