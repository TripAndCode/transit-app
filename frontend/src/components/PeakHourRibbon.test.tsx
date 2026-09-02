import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import "../i18n";
import { PeakHourRibbon } from "./PeakHourRibbon";
import type { OverviewPeakHour } from "../api/types";

// Mirrors the component's own H/PAD_TOP/PAD_BOTTOM constants: the chart's
// viewBox is 0..H tall, with the visible plot band inset by PAD_TOP/
// PAD_BOTTOM. A bar must never render outside [0, H] regardless of how
// extreme the underlying value/denominator ratio gets.
const H = 140;

describe("PeakHourRibbon", () => {
  it("keeps every bar within the chart's viewBox when the scale itself (peak_avg_min) is negative", () => {
    // All hours are early-running (negative avg delay). peak_avg_min is the
    // *least* negative value (hour 5, -1min) — denom in toY() — but hour 10
    // is far more negative (-10min). Before clamping y/bar_h to the plot
    // band, this ratio (v/denom = -10/-1 = 10) pushed the hour-10 bar's y
    // far above the chart (and its height far beyond the chart's own
    // height), rendering outside the SVG's visible area since the <svg> is
    // styled overflow: visible.
    const by_hour: (number | null)[] = new Array(24).fill(null);
    by_hour[5] = -1;
    by_hour[10] = -10;
    const peak_hour: OverviewPeakHour = {
      by_hour,
      peak_hour: 5,
      peak_avg_min: -1,
    };

    const { container } = render(<PeakHourRibbon peak_hour={peak_hour} />);
    const rects = container.querySelectorAll("rect");
    expect(rects.length).toBeGreaterThan(0);
    for (const rect of rects) {
      const y = Number(rect.getAttribute("y"));
      const height = Number(rect.getAttribute("height"));
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y + height).toBeLessThanOrEqual(H);
    }
  });
});
