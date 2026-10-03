// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../../test/cssRules";
const css = readFileSync(resolve(process.cwd(), "src/components/charts/compareBars.css"), "utf8");
describe("compareBars.css", () => {
  it("bars morph by transform on --dur-3 and rows travel by transform", () => {
    const bar = ruleBody(css, ".compare-bar__track i {");
    expect(decl(bar, "transform")).toBe("scaleX(var(--w, 0))");
    expect(decl(bar, "transform-origin")).toBe("left");
    expect(decl(bar, "transition")).toBe("transform var(--dur-3) var(--ease-out), background-color var(--dur-3) var(--ease-out)");
    expect(decl(ruleBody(css, ".compare-bar-row {"), "transition")).toBe("transform var(--dur-3) var(--ease-out), background var(--dur-1) var(--ease-out)");
    expect(css).not.toMatch(/transition:[^;]*\b(width|height|left|top)\b/);
  });
  it("the ghost is a dashed outline in the border colour", () => {
    expect(decl(ruleBody(css, ".compare-bar__ghost {"), "border")).toBe("1px dashed var(--border-subtle)");
  });
});
