// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./alertCenter.css"), "utf-8");

describe("alertCenter.css badge", () => {
  it("dims an all-acknowledged count below the base badge, and tones only a warning", () => {
    const base = decl(ruleBody(css, ".alert-center-badge {"), "color");
    expect(decl(ruleBody(css, ".alert-center-badge--muted {"), "color")).not.toBe(base);
    expect(decl(ruleBody(css, ".alert-center-badge--warn {"), "color")).toBe("var(--color-warning-text)");
  });
});
