// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../../test/cssRules";

const css = readFileSync(resolve(process.cwd(), "src/components/charts/HourlyHeatmap.css"), "utf8");
const declarations = css.replace(/\/\*[\s\S]*?\*\//g, "");

describe("HourlyHeatmap.css", () => {
  it("gives a hovered cell a stroke ring and a brightness lift, never a transform", () => {
    const cell = ruleBody(declarations, ".heat-cell:hover");
    expect(decl(cell, "stroke")).toBe("var(--accent)");
    expect(decl(cell, "filter")).toBe("brightness(1.1)");
    expect(declarations).not.toMatch(/transform/);
  });

  it("leaves a focused cell's ring to the overlay instead of stacking a second one", () => {
    const focused = ruleBody(declarations, ".heat-cell:focus-visible");
    expect(decl(focused, "outline")).toBe("none");
    expect(decl(focused, "stroke")).toBeNull();
  });

  it("names no literal transition or animation duration", () => {
    expect(declarations).not.toMatch(/transition|animation/);
  });
});
