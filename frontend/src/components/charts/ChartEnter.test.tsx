import { describe, it, expect, vi, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useRef } from "react";
import { useDrawOn, staggerDelay } from "./ChartEnter";

function setReducedMotion(reduce: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList);
}

function Harness() {
  const ref = useRef<SVGPathElement | null>(null);
  useDrawOn(ref);
  return (
    <svg>
      <path ref={ref} d="M0,0 L10,10" data-testid="p" />
    </svg>
  );
}

// jsdom does not implement SVGPathElement/SVGPolylineElement/SVGGeometryElement
// at all -- every SVG element it creates is a plain SVGElement instance with
// no getTotalLength -- so the mock goes on SVGElement.prototype directly
// (and is removed again, since the property does not exist there natively).
function mockGetTotalLength(impl: () => number) {
  (SVGElement.prototype as unknown as { getTotalLength: () => number }).getTotalLength = impl;
}

describe("useDrawOn", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete (SVGElement.prototype as unknown as { getTotalLength?: () => number }).getTotalLength;
  });

  it("sets --len from getTotalLength() and adds the draw-on class", () => {
    setReducedMotion(false);
    mockGetTotalLength(() => 123.4);
    const { getByTestId } = render(<Harness />);
    const path = getByTestId("p");
    expect(path.classList.contains("chart-draw-on")).toBe(true);
    expect(path.style.getPropertyValue("--len")).toBe("123.4");
  });

  it("does nothing under prefers-reduced-motion: reduce", () => {
    setReducedMotion(true);
    mockGetTotalLength(() => 123.4);
    const { getByTestId } = render(<Harness />);
    const path = getByTestId("p");
    expect(path.classList.contains("chart-draw-on")).toBe(false);
    expect(path.style.getPropertyValue("--len")).toBe("");
  });

  it("degrades quietly when getTotalLength is unavailable (e.g. unrendered jsdom geometry)", () => {
    setReducedMotion(false);
    // No mock installed at all -- exercises the real jsdom gap.
    expect(() => render(<Harness />)).not.toThrow();
  });
});

describe("staggerDelay", () => {
  it("steps the delay linearly with the index", () => {
    expect(staggerDelay(0)).toEqual({ transitionDelay: "0ms" });
    expect(staggerDelay(1)).toEqual({ transitionDelay: "6ms" });
    expect(staggerDelay(10)).toEqual({ transitionDelay: "60ms" });
  });

  it("respects a custom step", () => {
    expect(staggerDelay(3, { step: 10 })).toEqual({ transitionDelay: "30ms" });
  });

  it("caps the delay so a large grid does not stagger forever", () => {
    expect(staggerDelay(1000)).toEqual({ transitionDelay: "900ms" });
    expect(staggerDelay(1000, { cap: 300 })).toEqual({ transitionDelay: "300ms" });
  });
});
