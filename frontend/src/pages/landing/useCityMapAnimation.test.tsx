import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useRef } from "react";
import { drawScene } from "./cityMapDraw";
import { useCityMapAnimation } from "./useCityMapAnimation";

vi.mock("./cityMapDraw", () => ({ drawScene: vi.fn() }));

function Harness() {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useCityMapAnimation(ref);
  return <canvas ref={ref} aria-hidden="true" />;
}

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

describe("useCityMapAnimation", () => {
  beforeEach(() => {
    vi.mocked(drawScene).mockClear();
    // jsdom ships no 2D canvas implementation; the hook bails out on a null
    // context, which would make every assertion below vacuously true.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      setTransform: () => {},
    } as unknown as CanvasRenderingContext2D);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("runs the rAF loop when motion is allowed", () => {
    setReducedMotion(false);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    render(<Harness />);
    expect(raf).toHaveBeenCalled();
  });

  it("draws one static frame and schedules nothing under reduced motion", () => {
    setReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    render(<Harness />);
    expect(raf).not.toHaveBeenCalled();
    expect(drawScene).toHaveBeenCalledTimes(1);
  });
});
