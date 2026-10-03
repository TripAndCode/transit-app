import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ShadedDays, ThresholdBand, VerticalMarker } from "./annotations";

/** The primitives are SVG fragments, so every case mounts them in a host <svg>. */
function renderSvg(children: React.ReactNode) {
  return render(<svg>{children}</svg>);
}

const identityY = (v: number) => 100 - v * 10;

describe("ThresholdBand", () => {
  it("spans from the threshold up to the top of the plot", () => {
    const { container } = renderSvg(<ThresholdBand min={5} max={8} toY={identityY} x={10} width={200} />);
    const rect = container.querySelector("[data-testid='threshold-band'] rect")!;
    expect(rect.getAttribute("y")).toBe("20"); // identityY(8)
    expect(rect.getAttribute("height")).toBe("30"); // identityY(5) - identityY(8)
    expect(rect.getAttribute("width")).toBe("200");
  });

  it("draws the threshold itself as an outline rule, not a fill", () => {
    const { container } = renderSvg(<ThresholdBand min={5} max={8} toY={identityY} x={10} width={200} />);
    const line = container.querySelector("[data-testid='threshold-band'] line")!;
    expect(line.getAttribute("y1")).toBe("50");
    expect(line.getAttribute("stroke-dasharray")).toBeTruthy();
  });

  it("renders its label when given one", () => {
    const { getByText } = renderSvg(
      <ThresholdBand min={5} max={8} toY={identityY} x={10} width={200} label="Severe" />,
    );
    expect(getByText("Severe")).toBeInTheDocument();
  });

  it("renders nothing when the threshold sits above the plotted maximum", () => {
    const { container } = renderSvg(<ThresholdBand min={5} max={5} toY={identityY} x={10} width={200} />);
    expect(container.querySelector("[data-testid='threshold-band']")).toBeNull();
  });
});

describe("VerticalMarker", () => {
  it("draws a full-height rule at x with its label", () => {
    const { container, getByText } = renderSvg(<VerticalMarker x={40} y1={10} y2={90} label="Rev" />);
    const line = container.querySelector("[data-testid='vertical-marker'] line")!;
    expect(line.getAttribute("x1")).toBe("40");
    expect(line.getAttribute("x2")).toBe("40");
    expect(line.getAttribute("y1")).toBe("10");
    expect(line.getAttribute("y2")).toBe("90");
    expect(getByText("Rev")).toBeInTheDocument();
  });

  it("carries a pointer tooltip on the rule itself when given one", () => {
    const { container } = renderSvg(<VerticalMarker x={40} y1={10} y2={90} title="Schedule revision" />);
    expect(container.querySelector("[data-testid='vertical-marker'] line title")?.textContent).toBe(
      "Schedule revision",
    );
  });

  it("distinguishes a highlight marker from a background context one", () => {
    const { container } = renderSvg(<VerticalMarker x={40} y1={10} y2={90} variant="highlight" />);
    expect(container.querySelector("[data-variant='highlight']")).not.toBeNull();
  });
});

describe("ShadedDays", () => {
  const toX = (i: number) => i * 10;

  it("merges consecutive days into one band", () => {
    const { container } = renderSvg(<ShadedDays days={[2, 3, 4]} toX={toX} bandWidth={10} y={0} height={50} />);
    const rects = container.querySelectorAll("[data-testid='shaded-days'] rect");
    expect(rects).toHaveLength(1);
    expect(rects[0].getAttribute("x")).toBe("20");
    expect(rects[0].getAttribute("width")).toBe("30");
  });

  it("keeps non-consecutive days as separate bands", () => {
    const { container } = renderSvg(<ShadedDays days={[1, 4, 5]} toX={toX} bandWidth={10} y={0} height={50} />);
    const rects = container.querySelectorAll("[data-testid='shaded-days'] rect");
    expect(rects).toHaveLength(2);
    expect(Array.from(rects).map((r) => r.getAttribute("width"))).toEqual(["10", "20"]);
  });

  it("sorts and de-duplicates the day list before merging", () => {
    const { container } = renderSvg(<ShadedDays days={[3, 1, 2, 2]} toX={toX} bandWidth={10} y={0} height={50} />);
    const rects = container.querySelectorAll("[data-testid='shaded-days'] rect");
    expect(rects).toHaveLength(1);
    expect(rects[0].getAttribute("width")).toBe("30");
  });

  it("renders nothing for an empty day list", () => {
    const { container } = renderSvg(<ShadedDays days={[]} toX={toX} bandWidth={10} y={0} height={50} />);
    expect(container.querySelector("[data-testid='shaded-days']")).toBeNull();
  });
});
