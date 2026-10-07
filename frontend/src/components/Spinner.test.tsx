import { render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { Spinner } from "./Spinner";
import { ruleBody, decl } from "../test/cssRules";

const uiCss = readFileSync(resolve(process.cwd(), "src/components/ui/ui.css"), "utf8");
const MOTION_ALLOWED = "@media (prefers-reduced-motion: no-preference)";

// ui.css holds several motion-allowed blocks (toast, overlay scrim, ...), so
// the spinner's is found by content rather than by being the first one.
function motionAllowedBlocks(): string[] {
  return [...uiCss.matchAll(/@media \(prefers-reduced-motion: no-preference\)/g)].map((m) =>
    ruleBody(uiCss.slice(m.index), MOTION_ALLOWED),
  );
}

describe("Spinner", () => {
  it("renders no inline <style> and carries the shared class", () => {
    const { container } = render(<Spinner />);
    expect(container.querySelector("style")).toBeNull();
    expect(container.querySelector("[data-spinner]")!.classList.contains("ui-spinner")).toBe(true);
  });
  it("puts no animation on the svg inline, where reduced motion could not reach it", () => {
    const { container } = render(<Spinner />);
    expect(container.querySelector("svg")!.style.animation).toBe("");
  });
  it("spins only under prefers-reduced-motion: no-preference and on a motion token", () => {
    const spinnerBlocks = motionAllowedBlocks().filter((b) => b.includes(".ui-spinner"));
    expect(spinnerBlocks).toHaveLength(1);
    expect(spinnerBlocks[0]).toMatch(
      /\.ui-spinner svg\s*\{[^}]*animation:\s*ui-spinner-rotate calc\(var\(--dur-4\) \* 0\.75\) linear infinite/,
    );
    const outside = motionAllowedBlocks().reduce((css, block) => css.replace(block, ""), uiCss);
    expect(outside).not.toMatch(/ui-spinner-rotate/);
  });
  it("keeps the static arc under reduced motion (no animation declared outside the motion block)", () => {
    expect(decl(ruleBody(uiCss, ".ui-spinner {"), "animation")).toBeNull();
  });
});
