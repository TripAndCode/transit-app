import { describe, it, expect } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import "../i18n";
import { PeakHourRibbon } from "./PeakHourRibbon";
import type { OverviewPeakHour } from "../api/types";

// Mirrors the component's own W/H/PAD_*/CELL_W constants: the chart's
// viewBox is 0..W wide, 0..H tall, with the visible plot band inset by
// PAD_TOP/PAD_BOTTOM. A bar (or the hover tooltip it can trigger) must
// never render outside [0, H] regardless of how extreme the underlying
// value/denominator ratio gets.
const W = 660;
const H = 140;
const PAD_TOP = 28;
const PAD_BOTTOM = 22;
const CELL_W = (W - 32) / 24;

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

  it("keeps the hover tooltip within the chart's plot band for the same negative-scale dataset", () => {
    // Same dataset/root cause as the bar test above: hovering over hour 10
    // (far more negative than the peak/denom at hour 5) used to compute a
    // tooltip y far outside the chart via the same unclamped toY().
    const by_hour: (number | null)[] = new Array(24).fill(null);
    by_hour[5] = -1;
    by_hour[10] = -10;
    const peak_hour: OverviewPeakHour = {
      by_hour,
      peak_hour: 5,
      peak_avg_min: -1,
    };

    const { container } = render(<PeakHourRibbon peak_hour={peak_hour} />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // Mock a 1:1 pixel mapping (rect size == viewBox size) so clientX/Y map
    // directly to SVG-space coordinates without needing to reverse a scale.
    svg!.getBoundingClientRect = () =>
      ({ width: W, height: H, left: 0, top: 0 }) as DOMRect;

    // Land inside hour 10's cell: idx = floor(localX / CELL_W) === 10.
    const clientX = 10 * CELL_W + CELL_W / 2;
    fireEvent.mouseMove(svg!, { clientX, clientY: 0 });

    const tooltip = container.querySelector(".ov-tooltip") as HTMLElement | null;
    expect(tooltip).not.toBeNull();
    const top = parseFloat(tooltip!.style.top);
    expect(top).toBeGreaterThanOrEqual(PAD_TOP);
    expect(top).toBeLessThanOrEqual(H - PAD_BOTTOM);
  });
});
