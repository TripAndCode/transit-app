// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

// An SVG drawn at a viewBox and stretched to its box scales its text with it:
// a `fontSize="10"` label renders below the 12px CJK floor as soon as the
// chart is narrower than its viewBox. Charts stretched by width draw their
// labels as HTML over the plot instead; fixed-height charts keep a minimum
// width so the only remaining scale is the height ratio.
const src = (p: string) => readFileSync(path.resolve(process.cwd(), "src", p), "utf8");
/** Every SVG `fontSize` attribute in a file, as written and as a number. A
 *  size named by a constant resolves through that file's `const NAME =
 *  <number>`; any other expression is NaN, which fails every floor below. */
function fontSizes(code: string): Array<readonly [string, number]> {
  const constants = new Map([...code.matchAll(/const (\w+) = (\d+(?:\.\d+)?);/g)].map((m) => [m[1], Number(m[2])]));
  return [...code.matchAll(/fontSize=(?:"([^"]*)"|\{([^}]*)\})/g)].map((m) => {
    const value = (m[1] ?? m[2]).trim();
    if (/^\d+(?:\.\d+)?$/.test(value)) return [m[0], Number(value)] as const;
    return [m[0], constants.get(value) ?? NaN] as const;
  });
}

describe("SVG text floor", () => {
  it("reads every fontSize form, and fails one it cannot resolve rather than skipping it", () => {
    const code = 'const SIZE = 13;\n<text fontSize="12" /><text fontSize={14} /><text fontSize={SIZE} /><text fontSize={wide ? 10 : 12} /><text fontSize={tokens.small} />';
    const sizes = fontSizes(code).map(([, size]) => size);
    expect(sizes.slice(0, 3)).toEqual([12, 14, 13]);
    expect(sizes.slice(3)).toEqual([NaN, NaN]);
  });

  it("stretched charts draw their labels in HTML, never as SVG text", () => {
    for (const f of ["components/PeakHourRibbon.tsx", "components/ServiceSplit.tsx", "components/ConcentrationBar.tsx"]) {
      expect(src(f), f).not.toMatch(/<text[\s>]/);
    }
  });

  it("fixed-height charts keep a width floor at which their labels clear 12px", () => {
    const rule = src("styles/focusedAnalysis.css").match(/\.focus-chart\s*\{([^}]*)\}/)![1];
    const floor = Number(rule.match(/min-width:\s*(\d+)px/)![1]);
    const boxHeight = Number(rule.match(/(?:^|;)\s*height:\s*(\d+)px/)![1]);
    for (const f of ["components/analysis/StopChart.tsx", "components/analysis/PeriodChart.tsx"]) {
      const [, vbW, vbH] = src(f).match(/viewBox="0 0 (\d+) (\d+)"/)!.map(Number);
      // At the floor the chart draws at the smaller of its width and height scales.
      const scale = Math.min(floor / vbW, boxHeight / vbH);
      for (const g of [f, "components/analysis/ChartAxis.tsx"]) {
        for (const m of src(g).matchAll(/fontSize=\{?"?(\d+)"?\}?/g)) {
          expect(Number(m[1]) * scale, `${g} in ${f}: ${m[0]}`).toBeGreaterThanOrEqual(12);
        }
      }
    }
  });

  it("charts drawn at their pixel size set every SVG label at 12px or more", () => {
    for (const f of [
      "components/charts/DailyChart.tsx",
      "components/charts/HourlyHeatmap.tsx",
      "components/charts/annotations.tsx",
      "components/InlineSparkline.tsx",
    ]) {
      for (const [text, size] of fontSizes(src(f))) expect(size, `${f}: ${text}`).toBeGreaterThanOrEqual(12);
    }
  });

  it("the Marey diagram keeps a width floor at which its labels clear 12px", () => {
    const f = "components/charts/MareyDiagram.tsx";
    const rule = src("styles/focusedAnalysis.css").match(/\.marey__chart\s*\{([^}]*)\}/)![1];
    const floor = Number(rule.match(/min-width:\s*(\d+)px/)![1]);
    const boxHeight = Number(rule.match(/(?:^|;)\s*height:\s*(\d+)px/)![1]);
    const vbW = Number(src(f).match(/viewBox=\{`0 0 (\d+) /)![1]);
    const vbH = Number(src(f).match(/const VIEW_HEIGHT = (\d+);/)![1]);
    const scale = Math.min(floor / vbW, boxHeight / vbH);
    for (const [text, size] of fontSizes(src(f))) expect(size * scale, `${f}: ${text}`).toBeGreaterThanOrEqual(12);
  });

  it("the admin run timeline never draws narrower than its viewBox, so its labels keep their size", () => {
    const f = "pages/admin/RunTimeline.tsx";
    expect(src(f)).toMatch(/minWidth:\s*VIEW_WIDTH/);
    for (const [text, size] of fontSizes(src(f))) expect(size, `${f}: ${text}`).toBeGreaterThanOrEqual(12);
  });

  it("the landing page's stop chart keeps a floor at every width that fits its desktop figure", () => {
    const css = src("pages/landing/ScrollNarrative.css").replace(/@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^{}]*\}/g, "");
    const floor = Number(css.match(/\.landing-narrative-section__figure \.focus-chart\s*\{[^}]*min-width:\s*(\d+)px/)![1]);
    expect(floor).toBeGreaterThanOrEqual(700);
    // The desktop figure's inner width, so a desktop visit never scrolls.
    expect(floor).toBeLessThanOrEqual(760);
  });
});
