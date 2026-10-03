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
    expect(src("styles/focusedAnalysis.css")).toMatch(/\.focus-chart\s*\{[^}]*min-width:\s*780px/);
    for (const f of ["components/analysis/ChartAxis.tsx", "components/analysis/StopChart.tsx", "components/analysis/PeriodChart.tsx"]) {
      for (const m of src(f).matchAll(/fontSize=\{?"?(\d+)"?\}?/g)) {
        expect(Number(m[1]) * (300 / 335), `${f}: ${m[0]}`).toBeGreaterThanOrEqual(12);
      }
    }
  });
});
