import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

describe("delay figures stay calm", () => {
  it("prints the route page's headline delay in the text colour", () => {
    expect(decl(ruleBody(css("src/styles/focusedAnalysis.css"), ".focus-aside strong"), "color")).toBe("var(--text-primary)");
  });

  it("marks a needs-attention count in the warning amber, not alarm red", () => {
    expect(decl(ruleBody(css("src/components/StatTile.css"), ".stat-tile__value--flagged"), "color")).toBe(
      "var(--color-warning-text)",
    );
  });
});
