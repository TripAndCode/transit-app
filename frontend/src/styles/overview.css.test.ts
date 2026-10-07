import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ruleBody, decl } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./overview.css"), "utf-8");
const uiCss = readFileSync(resolve(__dirname, "../components/ui/ui.css"), "utf-8");

describe("overview.css page width", () => {
  it(".ov-page fills its parent, so a long headline cannot widen it past a phone screen", () => {
    const body = css.match(/\.ov-page\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(body).toMatch(/(^|;|\s)width:\s*100%/);
  });
});

describe("overview.css hover affordances change colour or opacity only", () => {
  it("the pareto track does not grow on hover", () => {
    expect(css).not.toMatch(/\.ov-pareto-row:hover \.ov-pareto-track\s*\{[^}]*height/);
    expect(decl(ruleBody(css, ".ov-pareto-fill"), "transition")).not.toMatch(/height/);
  });

  it("the pareto fill's rest and hover opacity both derive from its rank", () => {
    expect(decl(ruleBody(css, ".ov-pareto-fill"), "opacity")).toMatch(/var\(--rank-opacity, 1\)/);
    expect(decl(ruleBody(css, ".ov-pareto-row:hover .ov-pareto-fill"), "opacity")).toMatch(/var\(--rank-opacity, 1\)/);
  });

  it("a clickable card neither lifts nor changes elevation on hover", () => {
    const hover = css.match(/\.ov-card\.ov-clickable:hover\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(hover).not.toMatch(/transform|box-shadow/);
    expect(hover).toMatch(/background/);
    const uiHover = uiCss.match(/\.ui-card--clickable:hover\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(uiHover).not.toMatch(/transform|box-shadow/);
  });
});
