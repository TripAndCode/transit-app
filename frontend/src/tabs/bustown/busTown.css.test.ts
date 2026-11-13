// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./busTown.css"), "utf-8");

/** Every top-level selector a stylesheet declares a rule for, comma lists
 *  split apart — so a scoping check can tell a bare `.scrub-input` from
 *  `.bus-town-controls .scrub-input` rather than matching either as a
 *  substring of the other. This file has no `@media`/`@supports` blocks, so
 *  a flat split on `}` is enough; a file that gained one would need the
 *  brace-balancing `withoutBlocks` does. */
function topLevelSelectors(stylesheet: string): string[] {
  const withoutComments = stylesheet.replace(/\/\*[\s\S]*?\*\//g, "");
  return withoutComments
    .split("}")
    .map((rule) => rule.split("{")[0])
    .flatMap((header) => header.split(","))
    .map((selector) => selector.trim())
    .filter(Boolean);
}

describe("busTown.css scrubber scope", () => {
  it("never styles a bare .scrub-input, which the Map tab's PlaybackRail and MareyDiagram also use", () => {
    const unscoped = topLevelSelectors(css).filter(
      (selector) => /\.scrub-input\b/.test(selector) && !selector.startsWith(".bus-town-controls "),
    );
    expect(unscoped).toEqual([]);
  });

  it("scopes the scrubber rule to Bus Town's own controls", () => {
    expect(decl(ruleBody(css, ".bus-town-controls .scrub-input {"), "flex")).toBe("1 1 160px");
  });
});
