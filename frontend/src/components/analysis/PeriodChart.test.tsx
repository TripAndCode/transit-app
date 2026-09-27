import { describe, it, expect, afterEach } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PeriodChart } from "./PeriodChart";
import type { TrendDay } from "../../api/types";

function consecutiveDays(count: number): TrendDay[] {
  return Array.from({ length: count }, (_, i) => ({
    date: `2026-05-${String(10 + i).padStart(2, "0")}`,
    avg_min: 1 + (i % 3),
    samples: 10,
    top_offenders: [],
  }));
}

// jsdom implements no SVG geometry interfaces, so the mock goes on
// SVGElement.prototype. Deriving the length from `d` makes a path with more
// days measure longer, the way a real browser would.
function mockLengthFromPathData() {
  (SVGElement.prototype as unknown as { getTotalLength: (this: SVGElement) => number }).getTotalLength =
    function () {
      return (this.getAttribute("d") ?? "").length;
    };
}

describe("PeriodChart draw-on", () => {
  afterEach(() => {
    delete (SVGElement.prototype as unknown as { getTotalLength?: () => number }).getTotalLength;
  });

  it("keeps --len equal to the current path when new data re-renders the mounted chart", () => {
    mockLengthFromPathData();
    const { container, rerender } = renderWithProviders(<PeriodChart days={consecutiveDays(3)} />);
    const line = container.querySelector("path");
    if (!line) throw new Error("PeriodChart rendered no line path");
    const before = line.style.getPropertyValue("--len");
    expect(line.classList.contains("chart-draw-on")).toBe(true);

    rerender(<PeriodChart days={consecutiveDays(10)} />);
    expect(container.querySelector("path")).toBe(line);
    const d = line.getAttribute("d") ?? "";
    expect(line.style.getPropertyValue("--len")).toBe(String(d.length));
    expect(line.style.getPropertyValue("--len")).not.toBe(before);
  });
});
