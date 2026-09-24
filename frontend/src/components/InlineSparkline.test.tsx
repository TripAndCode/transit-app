import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { InlineSparkline } from "./InlineSparkline";
import { periodMean } from "./periodMean";

describe("InlineSparkline", () => {
  it("does not set preserveAspectRatio by default", () => {
    const { container } = render(<InlineSparkline points={[1, 2, 3]} />);
    expect(container.querySelector("svg")?.getAttribute("preserveAspectRatio")).toBeNull();
  });

  it("passes preserveAspectRatio through, for a full-bleed background use over a fixed-ratio box", () => {
    const { container } = render(<InlineSparkline points={[1, 2, 3]} preserveAspectRatio="none" />);
    expect(container.querySelector("svg")?.getAttribute("preserveAspectRatio")).toBe("none");
  });

  it("draws no reference line unless one is asked for", () => {
    const { container } = render(<InlineSparkline points={[1, 2, 3]} />);
    expect(container.querySelector('[data-testid="sparkline-baseline"]')).toBeNull();
  });

  it("draws a dashed full-width reference line at the requested value", () => {
    const { container } = render(
      <InlineSparkline points={[0, 10]} width={100} height={50} showLabels={false} baseline={5} />,
    );
    const line = container.querySelector('[data-testid="sparkline-baseline"]');
    expect(line).toBeTruthy();
    expect(line?.getAttribute("x1")).toBe("0");
    expect(line?.getAttribute("x2")).toBe("100");
    // Midway between the series min and max, so the reader can see which days
    // sat above the reference and which below.
    expect(Number(line?.getAttribute("y1"))).toBeCloseTo(25, 1);
    expect(line?.getAttribute("y1")).toBe(line?.getAttribute("y2"));
    expect(line?.getAttribute("stroke-dasharray")).toBeTruthy();
  });

  it("carries no SVG text for the reference line -- a stretched viewBox would distort it", () => {
    const { container } = render(
      <InlineSparkline points={[0, 10]} showLabels={false} baseline={5} preserveAspectRatio="none" />,
    );
    expect(container.querySelectorAll("text")).toHaveLength(0);
  });

  it("periodMean averages the series, and reports nothing for an empty one", () => {
    expect(periodMean([1, 2, 6])).toBe(3);
    expect(periodMean([])).toBeNull();
  });
});
