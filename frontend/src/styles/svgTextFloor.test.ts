// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";

// Scope: the charts this guard names below. DailyChart, HourlyHeatmap, the
// chart annotations, MareyDiagram, InlineSparkline and the admin run timeline
// still draw SVG text and are outside it.
//
// An SVG drawn at a viewBox and stretched to its box scales its text with it:
// a `fontSize="10"` label renders below the 12px CJK floor as soon as the
// chart is narrower than its viewBox. Charts stretched by width draw their
// labels as HTML over the plot instead; fixed-height charts keep a minimum
// width so the only remaining scale is the height ratio.
const src = (p: string) => readFileSync(path.resolve(process.cwd(), "src", p), "utf8");

describe("SVG text floor", () => {
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

  it("the landing page's stop chart keeps a floor at every width that fits its desktop figure", () => {
    const css = src("pages/landing/ScrollNarrative.css").replace(/@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^{}]*\}/g, "");
    const floor = Number(css.match(/\.landing-narrative-section__figure \.focus-chart\s*\{[^}]*min-width:\s*(\d+)px/)![1]);
    expect(floor).toBeGreaterThanOrEqual(700);
    // The desktop figure's inner width, so a desktop visit never scrolls.
    expect(floor).toBeLessThanOrEqual(760);
  });
});
