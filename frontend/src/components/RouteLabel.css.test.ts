// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./RouteLabel.css"), "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");

describe("RouteLabel.css plate column", () => {
  it("lays a listed label onto its list's plate and name tracks", () => {
    const label = ruleBody(css, ".route-label.route-label--plate-column {");
    expect(decl(label, "display")).toBe("grid");
    expect(decl(label, "grid-column")).toBe("1 / -1");
    expect(decl(label, "grid-template-columns")).toBe("subgrid");
    // The plate brings its own spacing, so a list with no plate at all has a
    // zero-width plate track and its names start flush.
    expect(decl(label, "column-gap")).toBe("0");
    expect(decl(ruleBody(css, ".route-label.route-label--plate-column > .route-label__plate {"), "margin-inline-end")).toBe("0.6em");
  });

  it("puts everything but the plate in the name's track, plate or none", () => {
    const rest = ruleBody(css, ".route-label.route-label--plate-column > :not(.route-label__plate) {");
    expect(decl(rest, "grid-column")).toBe("2 / -1");
  });

  it("comes after the label's own grid rules, which it overrides at equal specificity", () => {
    expect(css.indexOf(".route-label.route-label--plate-column {")).toBeGreaterThan(
      css.indexOf(".route-label--plated .route-label__ends {"),
    );
    expect(css.indexOf(".route-label.route-label--plate-column {")).toBeGreaterThan(
      css.indexOf(".route-label--full.route-label--plated {"),
    );
  });
});
