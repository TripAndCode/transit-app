import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useRef } from "react";
import { drawHeroFrame, type HeroLabels } from "./heroMapDraw";
import { SEQUENCE_END } from "./heroMapTimeline";
import { useHeroMapAnimation } from "./useHeroMapAnimation";

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

  it("draws the finished still once and schedules nothing under reduced motion", () => {
    setReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    render(<Harness />);
    expect(raf).not.toHaveBeenCalled();
    expect(drawHeroFrame).toHaveBeenCalledTimes(1);
    const frame = vi.mocked(drawHeroFrame).mock.calls[0][3];
    expect(frame.t).toBe(SEQUENCE_END);
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
