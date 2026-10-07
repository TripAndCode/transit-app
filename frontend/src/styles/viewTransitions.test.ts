// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { decl, ruleBody } from "../test/cssRules";

const css = readFileSync(resolve(__dirname, "./viewTransitions.css"), "utf-8");
const reportTableCss = readFileSync(resolve(__dirname, "../components/ReportTable.css"), "utf-8");

describe("screen transitions", () => {
  it("animate only the routed pane, leaving the rail and top bar live and clickable", () => {
    expect(decl(ruleBody(css, "html {"), "view-transition-name")).toBe("none");
    expect(decl(ruleBody(css, "::view-transition {"), "pointer-events")).toBe("none");
  });

  it("settle the new screen in with a 4 px rise where motion is welcome", () => {
    const allowed = ruleBody(css, "@media (prefers-reduced-motion: no-preference)");
    expect(decl(ruleBody(allowed, "::view-transition-new(.page-nav)"), "animation")).toBe(
      "page-nav-in var(--dur-2) var(--ease-out) both",
    );
    expect(ruleBody(allowed, "@keyframes page-nav-in")).toContain("translateY(4px)");
    expect(decl(ruleBody(allowed, "::view-transition-old(.page-nav)"), "animation")).toBe(
      "page-nav-out var(--dur-1) var(--ease-out) both",
    );
  });

  it("carry a clicked route label into the dossier title on --dur-2, only where motion is welcome", () => {
    const allowed = ruleBody(css, "@media (prefers-reduced-motion: no-preference)");
    const group = ruleBody(allowed, "::view-transition-group(.route-title)");
    expect(decl(group, "animation-duration")).toBe("var(--dur-2)");
    expect(decl(group, "animation-timing-function")).toBe("var(--ease-out)");
    expect(css.replace(allowed, "")).not.toContain("route-title");
    // A named element split across line boxes skips the whole transition.
    // Only on the travelling label: a whole-label box elsewhere would stop the
    // hover underline and detach the chevron from the last word.
    expect(decl(ruleBody(reportTableCss, ".report-route-link__label--travelling {"), "display")).toBe("inline-block");
    expect(reportTableCss).not.toMatch(/\.report-route-link__label\s*\{/);
  });

  it("keep only a short crossfade under reduced motion, which the blanket rule cannot reach", () => {
    const reduced = ruleBody(css, "@media (prefers-reduced-motion: reduce)");
    expect(reduced).not.toContain("translate");
    expect(decl(ruleBody(reduced, "::view-transition-group(.page-nav)"), "animation-duration")).toBe("150ms");
  });
});
