import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "../../styles/global.css"), "utf8");

describe("crossfilter dim channels", () => {
  it("dims a weekday-band cell to the focus value, whatever its own resting opacity", () => {
    // A low-confidence cell rests at 0.5; the dim must land it at --focus-dim,
    // not at the product of the two.
    expect(decl(ruleBody(css, ".focus-dim-filter"), "filter")).toBe(
      "opacity(min(1, calc(var(--focus-dim) / var(--cell-opacity, 1))))",
    );
  });

  it("does not transition a heat cell's dim, which would wait out its staggered entrance delay", () => {
    const body = ruleBody(css, ".chart-cell-enter.chart-focus-dimmable");
    expect(decl(body, "transition")).not.toContain("fill-opacity");
  });
});
