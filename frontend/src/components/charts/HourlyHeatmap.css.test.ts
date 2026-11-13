// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../../test/cssRules";

const css = readFileSync(resolve(process.cwd(), "src/components/charts/HourlyHeatmap.css"), "utf8");

describe("HourlyHeatmap.css", () => {
  it("gives a hovered or keyboard-focused cell a stroke ring and a brightness lift, never a transform", () => {
    const cell = ruleBody(css, ".heat-cell:hover,");
    expect(decl(cell, "stroke")).toBe("var(--accent)");
    expect(decl(cell, "filter")).toBe("brightness(1.1)");
    expect(css).not.toMatch(/transform/);
  });

  it("names no literal transition or animation duration", () => {
    expect(css).not.toMatch(/transition|animation/);
  });
});
