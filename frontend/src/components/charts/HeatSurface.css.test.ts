// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../../test/cssRules";
const css = readFileSync(resolve(process.cwd(), "src/components/charts/HeatSurface.css"), "utf8");
describe("HeatSurface.css", () => {
  it("cells morph colour and ring on --dur-3 and dim on --dur-1; never a size", () => {
    const cell = ruleBody(css, ".heat-surface__cell {");
    expect(decl(cell, "background")).toBe("var(--c, var(--none))");
    expect(decl(cell, "box-shadow")).toMatch(/inset 0 0 0 var\(--heat-ring, 0px\)/);
    expect(decl(cell, "transition")).toBe("background-color var(--dur-3) var(--ease-out), box-shadow var(--dur-3) var(--ease-out), opacity var(--dur-1) var(--ease-out)");
    expect(cell).not.toMatch(/transition:[^;]*(width|height)/);
  });
  it("hover and focus are an outline, not a lift", () => {
    expect(decl(ruleBody(css, ".heat-surface__cell:hover,"), "outline")).toBe("2px solid var(--accent)");
    expect(css).not.toMatch(/translateY|--el-2/);
  });
  it("no transition anywhere names a layout property", () => {
    for (const m of css.matchAll(/transition:([^;]+);/g)) expect(m[1]).not.toMatch(/\b(width|height|top|left|padding|margin)\b/);
  });
});
