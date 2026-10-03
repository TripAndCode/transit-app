import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useRef } from "react";
import type { HeroLabels } from "./heroCanvas";
import { drawHeroFrame } from "./heroMapDraw";
import { DURATION, LOOP_START } from "./heroMapTimeline";
import { scriptTime, useHeroMapAnimation } from "./useHeroMapAnimation";

vi.mock("./heroMapDraw", () => ({ drawHeroFrame: vi.fn() }));

const LABELS = {} as HeroLabels;

function Harness() {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useHeroMapAnimation(ref, LABELS);
  return <canvas ref={ref} aria-hidden="true" />;
}

function setReducedMotion(reduce: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: reduce && query.includes("prefers-reduced-motion"),
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  );
}

describe("scriptTime", () => {
  it("plays the first pass from zero, then resumes every later pass at LOOP_START", () => {
    expect(scriptTime(0)).toBe(0);
    expect(scriptTime(DURATION - 0.5)).toBe(DURATION - 0.5);
    expect(scriptTime(DURATION)).toBeCloseTo(LOOP_START);
    expect(scriptTime(DURATION + 1)).toBeCloseTo(LOOP_START + 1);
    expect(scriptTime(DURATION + (DURATION - LOOP_START))).toBeCloseTo(LOOP_START);
  });

  it("never replays the fly-in, however long the page stays open", () => {
    for (let e = DURATION; e < DURATION * 20; e += 0.37) expect(scriptTime(e)).toBeGreaterThanOrEqual(LOOP_START);
  });
});

describe("useHeroMapAnimation", () => {
  beforeEach(() => {
    vi.mocked(drawHeroFrame).mockClear();
    // jsdom has no 2D context; without this stub the hook bails out early and
    // every assertion below would pass vacuously.
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

  it("draws the settled live-operations frame once and schedules nothing under reduced motion", () => {
    setReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    render(<Harness />);
    expect(raf).not.toHaveBeenCalled();
    expect(drawHeroFrame).toHaveBeenCalledTimes(1);
    expect(vi.mocked(drawHeroFrame).mock.calls[0][3].t).toBe(LOOP_START);
  });

  it("does not advance the script while the tab is hidden", () => {
    setReducedMotion(false);
    let tick: FrameRequestCallback = () => {};
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      tick = cb;
      return 1;
    });
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    render(<Harness />);
    tick(performance.now() + 16);
    expect(drawHeroFrame).not.toHaveBeenCalled();
    hidden.mockReturnValue(false);
    tick(performance.now() + 32);
    expect(drawHeroFrame).toHaveBeenCalledTimes(1);
  });
});
