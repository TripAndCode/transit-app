// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { ruleBody, decl } from "../test/cssRules";

const css = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8");

describe("view transitions", () => {
  it("the shared route title and the root cross-fade run on --dur-2 with --ease-out", () => {
    const title = ruleBody(css, "::view-transition-old(route-title),");
    expect(decl(title, "animation-duration")).toBe("var(--dur-2)");
    expect(decl(title, "animation-timing-function")).toBe("var(--ease-out)");
    expect(decl(ruleBody(css, "::view-transition-old(root),"), "animation-duration")).toBe("var(--dur-2)");
  });
  it("the route fade yields while a view transition is active -- one entrance, not two", () => {
    expect(decl(ruleBody(css, "html:active-view-transition .route-enter {"), "animation")).toBe("none");
  });
  it("reduced motion removes the pseudo-element animations entirely", () => {
    const reduce = ruleBody(css, "@media (prefers-reduced-motion: reduce)");
    expect(reduce).toMatch(/::view-transition-group\(\*\),\s*::view-transition-old\(\*\),\s*::view-transition-new\(\*\)\s*\{[^}]*animation:\s*none !important/);
  });
});
