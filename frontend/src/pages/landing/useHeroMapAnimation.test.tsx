import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useRef } from "react";
import type { HeroLabels } from "./heroCanvas";
import { drawHeroFrame } from "./heroMapDraw";
import { DURATION, REPLAY_START } from "./heroMapTimeline";
import { useHeroMapAnimation } from "./useHeroMapAnimation";

vi.mock("./heroMapDraw", () => ({ drawHeroFrame: vi.fn() }));

const LABELS = {} as HeroLabels;

function Harness() {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const { playback, toggle } = useHeroMapAnimation(ref, LABELS);
  return (
    <>
      <canvas ref={ref} aria-hidden="true" />
      <button type="button" onClick={toggle}>
        {playback}
      </button>
    </>
  );
}

/** A driveable requestAnimationFrame: `step` runs the pending frame `ms`
 *  later and reports whether the loop asked for another. */
function driveFrames() {
  let pending: FrameRequestCallback | null = null;
  let now = performance.now();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
    pending = cb;
    return 1;
  });
  const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {
    pending = null;
  });
  return {
    cancel,
    step(ms = 100) {
      const cb = pending;
      pending = null;
      now += ms;
      if (cb) act(() => cb(now));
      return pending !== null;
    },
    runToEnd() {
      let frames = 0;
      while (this.step() && frames < 10_000) frames++;
      return frames;
    },
  };
}

const drawnAt = () => vi.mocked(drawHeroFrame).mock.calls.map((call) => call[3].t);


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

describe("useHeroMapAnimation playback", () => {
  beforeEach(() => {
    vi.mocked(drawHeroFrame).mockClear();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      setTransform: () => {},
    } as unknown as CanvasRenderingContext2D);
    setReducedMotion(false);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("plays the script once, stops on its last frame and asks for no more frames", () => {
    const frames = driveFrames();
    render(<Harness />);
    expect(screen.getByRole("button")).toHaveTextContent("playing");
    frames.runToEnd();
    expect(drawnAt().at(-1)).toBe(DURATION);
    expect(Math.max(...drawnAt())).toBe(DURATION);
    expect(screen.getByRole("button")).toHaveTextContent("ended");
    expect(frames.step()).toBe(false);
  });

  it("pauses on the frame on screen and resumes from it", () => {
    const frames = driveFrames();
    render(<Harness />);
    for (let i = 0; i < 30; i++) frames.step();
    const pausedAt = drawnAt().at(-1)!;
    act(() => screen.getByRole("button").click());
    expect(screen.getByRole("button")).toHaveTextContent("paused");
    expect(frames.step()).toBe(false);
    expect(drawnAt().at(-1)).toBe(pausedAt);
    act(() => screen.getByRole("button").click());
    expect(screen.getByRole("button")).toHaveTextContent("playing");
    frames.step();
    expect(drawnAt().at(-1)).toBeGreaterThan(pausedAt);
    expect(drawnAt().at(-1)).toBeLessThan(pausedAt + 0.2);
  });

  it("replays from REPLAY_START, after the fly-in, once it has ended", () => {
    const frames = driveFrames();
    render(<Harness />);
    frames.runToEnd();
    vi.mocked(drawHeroFrame).mockClear();
    act(() => screen.getByRole("button").click());
    expect(screen.getByRole("button")).toHaveTextContent("playing");
    expect(drawnAt()[0]).toBe(REPLAY_START);
    frames.runToEnd();
    expect(Math.min(...drawnAt())).toBe(REPLAY_START);
    expect(drawnAt().at(-1)).toBe(DURATION);
    expect(screen.getByRole("button")).toHaveTextContent("ended");
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
    expect(screen.getByRole("button")).toHaveTextContent("still");
    expect(drawHeroFrame).toHaveBeenCalledTimes(1);
    expect(vi.mocked(drawHeroFrame).mock.calls[0][3].t).toBe(REPLAY_START);
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
