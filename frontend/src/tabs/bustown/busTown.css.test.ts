// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../../test/cssRules";

const css = readFileSync(resolve(__dirname, "./busTown.css"), "utf-8");

/** Every selector a stylesheet declares a rule for, including rules inside
 *  `@media`, comma lists split apart — so a scoping check can tell a bare
 *  `.scrub-input` from `.bus-town-controls .scrub-input`, and a rule added
 *  inside the phone block is not exempt. Reads the text before each `{`
 *  rather than splitting on `}`: a flat split on `}` loses the first selector
 *  after an at-rule's `{`, because it ends up glued to the at-rule's prelude.
 *  Not for stylesheets with `@keyframes`, whose stops would read as
 *  selectors. */
function allSelectors(stylesheet: string): string[] {
  const src = stylesheet.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: string[] = [];
  for (const [head] of src.matchAll(/[^{}]+(?=\{)/g)) {
    const trimmed = head.trim();
    if (trimmed.startsWith("@")) continue;
    for (const selector of trimmed.split(",")) {
      const s = selector.trim();
      if (s) out.push(s);
    }
  }
  return out;
}

const PHONE_MEDIA = "@media (max-width: 640px) {";

describe("busTown.css scrubber scope", () => {
  it("never styles a bare .scrub-input, which the Map tab's PlaybackRail and MareyDiagram also use", () => {
    const selectors = allSelectors(css);
    // Pins that the walker actually found rules, including one nested inside
    // the phone @media block, rather than passing on an empty list.
    expect(selectors).toContain(".bus-town-controls .scrub-input");
    expect(selectors).toContain(".bus-town-page .ui-page-header__title");
    const unscoped = selectors.filter(
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
    expect(decl(ruleBody(phone, ".bus-town-page .ui-page-header__text {"), "gap")).toBe("2px");
  });

  it("makes the back link and the day picker share a line box instead of forcing two block lines", () => {
    expect(decl(ruleBody(phone, ".bus-town-back-link {"), "margin")).toBe("0 12px 10px 0");
    expect(decl(ruleBody(phone, ".bus-town-back-link {"), "vertical-align")).toBe("middle");
    expect(decl(ruleBody(phone, ".bus-town-day-picker {"), "display")).toBe("inline-flex");
    expect(decl(ruleBody(phone, ".bus-town-day-picker {"), "margin")).toBe("0 0 10px");
    expect(decl(ruleBody(phone, ".bus-town-day-picker {"), "vertical-align")).toBe("middle");
  });

  it("narrows the page and stage padding so the canvas gets (nearly) the full content width", () => {
    expect(decl(ruleBody(phone, ".bus-town-page {"), "padding")).toBe("24px 16px 56px");
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
    expect(decl(ruleBody(phone, ".bus-town-speed-pill {"), "padding")).toBe("4px 12px");
  });

  it("keeps the desktop play button at its own size outside the phone block", () => {
    const outsidePhone = css.slice(0, css.indexOf(PHONE_MEDIA));
    expect(decl(ruleBody(outsidePhone, ".bus-town-play-button {"), "width")).toBe("32px");
    expect(decl(ruleBody(outsidePhone, ".bus-town-play-button {"), "height")).toBe("32px");
  });
});
