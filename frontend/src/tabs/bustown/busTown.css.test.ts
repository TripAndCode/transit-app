// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./busTown.css"), "utf-8");

/** Every selector a stylesheet declares a rule for, at any nesting depth
 *  (including inside an `@media` block), comma lists split apart — so a
 *  scoping check can tell a bare `.scrub-input` from
 *  `.bus-town-controls .scrub-input` rather than matching either as a
 *  substring of the other, and so a rule added inside the phone `@media`
 *  block is not exempt from the same check. Brace-balanced, unlike a flat
 *  split on `}`, so a nested block's own declarations never leak into the
 *  header text of the rule that follows it. */
function allSelectors(stylesheet: string): string[] {
  const src = stylesheet.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: string[] = [];
  let i = 0;
  function block(): void {
    while (i < src.length) {
      const open = src.indexOf("{", i);
      const close = src.indexOf("}", i);
      if (close !== -1 && (open === -1 || close < open)) {
        i = close + 1;
        return;
      }
      if (open === -1) {
        i = src.length;
        return;
      }
      const head = src.slice(i, open).trim();
      i = open + 1;
      if (head.startsWith("@media") || head.startsWith("@supports")) {
        block();
      } else if (head.startsWith("@")) {
        let depth = 1;
        while (depth > 0 && i < src.length) {
          if (src[i] === "{") depth += 1;
          else if (src[i] === "}") depth -= 1;
          i += 1;
        }
      } else {
        const end = src.indexOf("}", i);
        for (const selector of head.split(",")) {
          const trimmed = selector.trim();
          if (trimmed) out.push(trimmed);
        }
        i = end + 1;
      }
    }
  }
  block();
  return out;
}

const PHONE_MEDIA = "@media (max-width: 640px) {";

describe("busTown.css scrubber scope", () => {
  it("never styles a bare .scrub-input, which the Map tab's PlaybackRail and MareyDiagram also use", () => {
    const unscoped = allSelectors(css).filter(
      (selector) => /\.scrub-input\b/.test(selector) && !selector.startsWith(".bus-town-controls "),
    );
    expect(unscoped).toEqual([]);
  });

  it("scopes the scrubber rule to Bus Town's own controls", () => {
    expect(decl(ruleBody(css, ".bus-town-controls .scrub-input {"), "flex")).toBe("1 1 160px");
  });
});

describe("busTown.css phone layout (BP.sm)", () => {
  const phone = ruleBody(css, PHONE_MEDIA);

  it("shrinks the page title and tightens the header gap, scoped under the page's own root", () => {
    expect(decl(ruleBody(phone, ".bus-town-page .ui-page-header__title {"), "font-size")).toBe("var(--text-xl)");
    expect(decl(ruleBody(phone, ".bus-town-page .ui-page-header {"), "margin-bottom")).toBe("12px");
  });

  it("makes the back link and the day picker share a line box instead of forcing two block lines", () => {
    expect(decl(ruleBody(phone, ".bus-town-day-picker {"), "display")).toBe("inline-flex");
  });

  it("narrows the stage card's own padding so the canvas gets (nearly) the full content width", () => {
    expect(decl(ruleBody(phone, ".bus-town-stage {"), "padding")).toBe("10px 6px");
  });

  it("gives the canvas a taller box than the desktop 16 / 10 ratio", () => {
    expect(decl(ruleBody(phone, ".bus-town-canvas {"), "aspect-ratio")).toBe("4 / 3");
  });

  it("keeps the desktop canvas ratio outside the phone block", () => {
    const outsidePhone = css.slice(0, css.indexOf(PHONE_MEDIA));
    expect(decl(ruleBody(outsidePhone, ".bus-town-canvas {"), "aspect-ratio")).toBe("16 / 10");
  });

  it("grows the play button and the speed pills to the 44px touch-target floor", () => {
    expect(decl(ruleBody(phone, ".bus-town-play-button {"), "width")).toBe("44px");
    expect(decl(ruleBody(phone, ".bus-town-play-button {"), "height")).toBe("44px");
    expect(decl(ruleBody(phone, ".bus-town-speed-pill {"), "min-width")).toBe("44px");
    expect(decl(ruleBody(phone, ".bus-town-speed-pill {"), "min-height")).toBe("44px");
  });

  it("keeps the desktop play button at its own size outside the phone block", () => {
    const outsidePhone = css.slice(0, css.indexOf(PHONE_MEDIA));
    expect(decl(ruleBody(outsidePhone, ".bus-town-play-button {"), "width")).toBe("32px");
    expect(decl(ruleBody(outsidePhone, ".bus-town-play-button {"), "height")).toBe("32px");
  });
});
