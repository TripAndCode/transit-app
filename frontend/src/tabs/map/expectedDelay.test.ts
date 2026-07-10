import { describe, it, expect } from "vitest";
import { expectedDelayForHour } from "./expectedDelay";
import type { ForecastHeatmapCell } from "../../api/types";

function cell(dow: number, hour: number, expected_avg_min: number, samples: number): ForecastHeatmapCell {
  return { dow, hour, expected_avg_min, samples, low_confidence: samples < 30 };
}

describe("expectedDelayForHour", () => {
  it("pools all 7 dow values for the given hour when dowFilter is 'all'", () => {
    const cells = [
      cell(1, 8, 2.0, 10), // Mon 08:00
      cell(6, 8, 6.0, 10), // Sat 08:00
      cell(1, 9, 100.0, 10), // different hour, must be excluded
    ];
    const result = expectedDelayForHour(cells, 8, "all");
    // sample-weighted mean: (2*10 + 6*10) / 20 = 4.0
    expect(result).toBeCloseTo(4.0, 5);
  });

  it("filters to dow 1-5 for 'weekday'", () => {
    const cells = [
      cell(1, 8, 2.0, 10), // Mon (weekday)
      cell(6, 8, 100.0, 10), // Sat (weekend) — must be excluded
    ];
    const result = expectedDelayForHour(cells, 8, "weekday");
    expect(result).toBeCloseTo(2.0, 5);
  });

  it("filters to dow 6-7 for 'weekend'", () => {
    const cells = [
      cell(1, 8, 100.0, 10), // Mon (weekday) — must be excluded
      cell(6, 8, 3.0, 10), // Sat (weekend)
      cell(7, 8, 5.0, 10), // Sun (weekend)
    ];
    const result = expectedDelayForHour(cells, 8, "weekend");
    // (3*10 + 5*10) / 20 = 4.0
    expect(result).toBeCloseTo(4.0, 5);
  });

  it("returns null when no matching cell has any samples", () => {
    const cells = [cell(1, 8, 2.0, 0), cell(2, 8, 3.0, 0)];
    expect(expectedDelayForHour(cells, 8, "all")).toBeNull();
  });

  it("returns null for an empty cells array", () => {
    expect(expectedDelayForHour([], 8, "all")).toBeNull();
  });
});
