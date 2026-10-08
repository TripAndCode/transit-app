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

describe("SVG text floor", () => {
  it("stretched charts draw their labels in HTML, never as SVG text", () => {
    for (const f of ["components/PeakHourRibbon.tsx", "components/ServiceSplit.tsx", "components/ConcentrationBar.tsx"]) {
      expect(src(f), f).not.toMatch(/<text[\s>]/);
    }
  });

  it("fixed-height charts keep a 780px floor and labels that clear 12px at that scale", () => {
    const rule = src("styles/focusedAnalysis.css").match(/\.focus-chart\s*\{([^}]*)\}/)![1];
    expect(rule).toMatch(/min-width:\s*780px/);
    const boxHeight = Number(rule.match(/(?:^|;)\s*height:\s*(\d+)px/)![1]);
    // The tallest viewBox any of them draws in sets the smallest scale.
    const viewBoxHeight = Math.max(
      ...["components/analysis/StopChart.tsx", "components/analysis/PeriodChart.tsx"].map((f) =>
        Number(src(f).match(/viewBox="0 0 \d+ (\d+)"/)![1]),
      ),
    );
    for (const f of ["components/analysis/ChartAxis.tsx", "components/analysis/StopChart.tsx", "components/analysis/PeriodChart.tsx"]) {
      for (const m of src(f).matchAll(/fontSize=\{?"?(\d+)"?\}?/g)) {
        expect(Number(m[1]) * (boxHeight / viewBoxHeight), `${f}: ${m[0]}`).toBeGreaterThanOrEqual(12);
      }
    }
  });

  it("the landing page's stop chart keeps the same floor at every width", () => {
    const css = src("pages/landing/ScrollNarrative.css").replace(/@media[^{]*\{(?:[^{}]*\{[^}]*\})*[^{}]*\}/g, "");
    expect(css).toMatch(/\.landing-narrative-section__figure \.focus-chart\s*\{[^}]*min-width:\s*780px/);
  });
});
