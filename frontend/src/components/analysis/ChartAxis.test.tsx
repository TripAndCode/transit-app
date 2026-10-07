import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PeriodChart } from "./PeriodChart";
import { StopChart } from "./StopChart";
import type { RouteShapeStop } from "../../api/types";

function axisLabels(container: HTMLElement) {
  return [...container.querySelectorAll('text[text-anchor="end"]')].map((node) => node.textContent);
}

describe("focused-analysis chart axes", () => {
  it("labels the period chart's gridlines with round values", () => {
    const days = [2.4, 10.7, 6.1].map((avg_min, i) => ({ date: `2026-08-1${i}`, avg_min, samples: 10, top_offenders: [] }));
    const { container } = renderWithProviders(<PeriodChart days={days} />);
    expect(axisLabels(container)).toEqual(["0.0", "2.0", "4.0", "6.0", "8.0", "10.0", "12.0"]);
  });

  it("labels the stop chart's gridlines with round values", () => {
    const stops: RouteShapeStop[] = [4.8, 10.7, 7.2].map((avg_min, i) => ({
      stop_id: `S${i}`, stop_name: `Stop ${i}`, stop_sequence: i + 1, avg_min, samples: 5, lon: 140, lat: 40,
    }));
    const { container } = renderWithProviders(<StopChart stops={stops} previous={[]} selected={1} onSelect={() => {}} />);
    expect(axisLabels(container)).toEqual(["0.0", "2.0", "4.0", "6.0", "8.0", "10.0", "12.0"]);
  });
});
