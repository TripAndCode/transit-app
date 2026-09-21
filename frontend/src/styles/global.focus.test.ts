// @vitest-environment node
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const css = readFileSync(fileURLToPath(new URL("./global.css", import.meta.url)), "utf8");

describe("global.css focus-visible rules", () => {
  it("defines a --focus-ring token", () => {
    expect(css).toMatch(/--focus-ring:\s*var\(--accent\);/);
  });

  it("gives every native focusable element and role=button [tabindex] a visible focus-visible outline", () => {
    const rule = css.match(/((?:[a-z[][^{]*:focus-visible,?\s*)+)\{\s*outline:\s*2px solid var\(--focus-ring\);/);
    expect(rule).not.toBeNull();
    const selectors = rule![1];
    for (const selector of ["a", "button", "input", "select", "textarea", "summary", "[tabindex]"]) {
      expect(selectors).toContain(`${selector}:focus-visible`);
    }
  });

  it("no longer strips the outline on plain :focus for text inputs", () => {
    const rule = css.match(/input:focus,\s*select:focus,\s*textarea:focus\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).not.toMatch(/outline:\s*none/);
    expect(rule![1]).toMatch(/border-color:\s*var\(--accent\);/);
  });

  it("gives SVG role=button shapes a stroke/filter focus treatment instead of unreliable outline", () => {
    expect(css).toMatch(/\.svg-focus-ring:focus-visible\s*\{[^}]*outline:\s*none;[^}]*stroke:\s*var\(--focus-ring\);/);
  });
});
