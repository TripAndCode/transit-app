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

describe("PeriodChart date labels", () => {
  it("leaves out the every-Nth label that would crowd the last day's", () => {
    const days = Array.from({ length: 32 }, (_, i) => {
      const d = new Date(Date.UTC(2026, 4, 1 + i));
      return { date: d.toISOString().slice(0, 10), avg_min: 2, samples: 10, top_offenders: [] };
    });
    const { container } = renderWithProviders(<PeriodChart days={days} />);
    const labels = [...container.querySelectorAll("text")].map((t) => t.textContent).filter((l) => /^\d\d-\d\d$/.test(l ?? ""));
    expect(labels.at(-2)).toBe("05-26");
    expect(labels.at(-1)).toBe("06-01");
  });
});
