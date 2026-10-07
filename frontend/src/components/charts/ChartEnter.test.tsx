import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render } from "@testing-library/react";
import { StrictMode, useRef } from "react";
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

function Harness({ d = "M0,0 L10,10", tick = 0 }: { d?: string; tick?: number }) {
  const ref = useRef<SVGPathElement | null>(null);
  useDrawOn(ref);
  return (
    <svg data-tick={tick}>
      <path ref={ref} d={d} data-testid="p" />
    </svg>
  );
}

// jsdom does not implement SVGPathElement/SVGPolylineElement/SVGGeometryElement
// at all -- every SVG element it creates is a plain SVGElement instance with
// no getTotalLength -- so the mock goes on SVGElement.prototype directly
// (and is removed again, since the property does not exist there natively).
function mockGetTotalLength(impl: (this: SVGElement) => number) {
  (SVGElement.prototype as unknown as { getTotalLength: typeof impl }).getTotalLength = impl;
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

  it("re-measures --len when the mounted line's geometry changes, without replaying the entrance", () => {
    setReducedMotion(false);
    // A length that grows with `d`, so a longer path measures longer.
    mockGetTotalLength(function () {
      return (this.getAttribute("d") ?? "").length;
    });
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const short = "M0,0 L10,10";
    const long = "M0,0 L10,10 L20,0 L30,10 L40,0";
    const { getByTestId, rerender } = render(<Harness d={short} />);
    const path = getByTestId("p");
    expect(path.style.getPropertyValue("--len")).toBe(String(short.length));

    rerender(<Harness d={long} />);
    expect(getByTestId("p")).toBe(path);
    expect(path.style.getPropertyValue("--len")).toBe(String(long.length));
    expect(path.classList.contains("chart-draw-on")).toBe(true);
    expect(raf).toHaveBeenCalledTimes(1);
  });

  it("skips the length read on a re-render that leaves the geometry unchanged", () => {
    setReducedMotion(false);
    const getTotalLength = vi.fn(function (this: SVGElement) {
      return (this.getAttribute("d") ?? "").length;
    });
    mockGetTotalLength(getTotalLength);
    const { container, rerender } = render(<Harness d="M0,0 L10,10" tick={0} />);
    expect(getTotalLength).toHaveBeenCalledTimes(1);

    // `tick` only changes an attribute on the <svg>, proving the re-render
    // committed while the path's own geometry stayed the same.
    rerender(<Harness d="M0,0 L10,10" tick={1} />);
    expect(container.querySelector("svg")?.getAttribute("data-tick")).toBe("1");
    expect(getTotalLength).toHaveBeenCalledTimes(1);

    rerender(<Harness d="M0,0 L10,10 L20,0" tick={2} />);
    expect(getTotalLength).toHaveBeenCalledTimes(2);
  });

  it("retries a changed line when its first length measurement fails", () => {
    setReducedMotion(false);
    let measurable = true;
    mockGetTotalLength(function () {
      if (!measurable) throw new Error("geometry unavailable");
      return (this.getAttribute("d") ?? "").length;
    });
    const short = "M0,0 L10,10";
    const long = "M0,0 L10,10 L20,0 L30,10";
    const { getByTestId, rerender } = render(<Harness d={short} />);
    const path = getByTestId("p");
    expect(path.style.getPropertyValue("--len")).toBe(String(short.length));

    measurable = false;
    rerender(<Harness d={long} tick={1} />);
    expect(path.style.getPropertyValue("--len")).toBe(String(short.length));

    measurable = true;
    rerender(<Harness d={long} tick={2} />);
    expect(path.style.getPropertyValue("--len")).toBe(String(long.length));
  });

  it("does nothing under prefers-reduced-motion: reduce", () => {
    setReducedMotion(true);
    mockGetTotalLength(() => 123.4);
    const { getByTestId } = render(<Harness />);
    const path = getByTestId("p");
    expect(path.classList.contains("chart-draw-on")).toBe(false);
    expect(path.style.getPropertyValue("--len")).toBe("");
  });

  it("useDrawOn waits for `ready` and then draws exactly once", () => {
    setReducedMotion(false);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    function Line({ ready }: { ready: boolean }) {
      const ref = useRef<SVGPolylineElement | null>(null);
      useDrawOn(ref, ready);
      return <svg><polyline ref={ref} points="0,0 10,10" /></svg>;
    }
    mockGetTotalLength(() => 14);
    const { container, rerender } = render(<Line ready={false} />);
    const line = container.querySelector("polyline")!;
    expect(line.classList.contains("chart-draw-on")).toBe(false);
    rerender(<Line ready />);
    expect(line.classList.contains("chart-draw-on")).toBe(true);
    expect(raf).toHaveBeenCalledTimes(1);
    rerender(<Line ready={false} />);
    rerender(<Line ready />);
    expect(raf).toHaveBeenCalledTimes(1);
  });

  it("still lands drawn when its effect is torn down before the activating frame (StrictMode, data emptied)", () => {
    // StrictMode replays every effect once at mount, and `ready` can drop
    // before the next frame. Either way the line must not stay parked at its
    // undrawn dashoffset with the activating class never added.
    setReducedMotion(false);
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      frames.push(cb);
      return frames.length;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      frames[id - 1] = () => {};
    });
    mockGetTotalLength(() => 14);
    function Line({ ready }: { ready: boolean }) {
      const ref = useRef<SVGPolylineElement | null>(null);
      useDrawOn(ref, ready);
      return <svg><polyline ref={ref} points="0,0 10,10" /></svg>;
    }
    const { container, rerender } = render(
      <StrictMode>
        <Line ready />
      </StrictMode>,
    );
    rerender(
      <StrictMode>
        <Line ready={false} />
      </StrictMode>,
    );
    act(() => frames.splice(0).forEach((cb) => cb(0)));
    const line = container.querySelector("polyline")!;
    expect(line.classList.contains("chart-draw-on")).toBe(true);
    expect(line.classList.contains("chart-draw-on--active")).toBe(true);
  });

  it("degrades quietly when getTotalLength is unavailable (e.g. unrendered jsdom geometry)", () => {
    setReducedMotion(false);
    // No mock installed at all -- exercises the real jsdom gap.
    expect(() => render(<Harness />)).not.toThrow();
  });
});

describe("staggerDelay", () => {
  it("staggerDelay caps so that the last cell's delay plus a --dur-2 fade stays inside --dur-3", () => {
    expect(staggerDelay(0)).toEqual({ transitionDelay: "0ms" });
    expect(staggerDelay(10)).toEqual({ transitionDelay: "40ms" });
    expect(staggerDelay(10_000)).toEqual({ transitionDelay: "360ms" });
  });

  it("steps the delay linearly with the index", () => {
    expect(staggerDelay(1)).toEqual({ transitionDelay: "4ms" });
    expect(staggerDelay(2)).toEqual({ transitionDelay: "8ms" });
  });

  it("respects a custom step and cap", () => {
    expect(staggerDelay(3, { step: 10 })).toEqual({ transitionDelay: "30ms" });
    expect(staggerDelay(1000, { cap: 300 })).toEqual({ transitionDelay: "300ms" });
  });
});
