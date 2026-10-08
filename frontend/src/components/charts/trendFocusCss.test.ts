import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "../../styles/global.css"), "utf8");

describe("crossfilter dim channels", () => {
  it("does not transition a heat cell's dim, which would wait out its staggered entrance delay", () => {
    const body = ruleBody(css, ".chart-cell-enter.chart-focus-dimmable");
    expect(decl(body, "transition")).not.toContain("fill-opacity");
  });
});
