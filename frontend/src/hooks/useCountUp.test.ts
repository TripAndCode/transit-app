import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useCountUp } from "./useCountUp";

/** Captures every rAF callback so a test can advance the animation frame by
 *  frame with an explicit timestamp, instead of depending on real timing. */
function mockRaf() {
  const queue: FrameRequestCallback[] = [];
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
    queue.push(cb);
    return queue.length;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  return {
    flush(now: number) {
      const cbs = queue.splice(0, queue.length);
      act(() => {
        cbs.forEach((cb) => cb(now));
      });
    },
  };
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

describe("useCountUp", () => {
  beforeEach(() => {
    setReducedMotion(false);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("counts up from 0 on first paint when motion is allowed", () => {
    const raf = mockRaf();
    const { result } = renderHook(() => useCountUp(42, { duration: 600, decimals: 1 }));
    // The figure arrives at 0 and climbs, rather than being printed at its
    // final value before the tile it lives in has finished appearing.
    expect(result.current).toBe(0);

    raf.flush(0);
    expect(result.current).toBe(0);
    raf.flush(300);
    expect(result.current).toBeGreaterThan(0);
    expect(result.current).toBeLessThan(42);
    raf.flush(600);
    expect(result.current).toBe(42);
  });

  it("prints the value outright on first paint under prefers-reduced-motion: reduce", () => {
    setReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result } = renderHook(() => useCountUp(42));
    expect(result.current).toBe(42);
    expect(raf).not.toHaveBeenCalled();
  });

  it("prints the value outright on first paint when duration is 0", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result } = renderHook(() => useCountUp(42, { duration: 0 }));
    expect(result.current).toBe(42);
    expect(raf).not.toHaveBeenCalled();
  });

  it("schedules no frames on first paint for a value of 0 -- nothing to count up to", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result } = renderHook(() => useCountUp(0));
    expect(result.current).toBe(0);
    expect(raf).not.toHaveBeenCalled();
  });

  it("eases toward a new value over successive frames and reaches the exact target", () => {
    const raf = mockRaf();
    const { result, rerender } = renderHook(({ value }) => useCountUp(value, { duration: 600, decimals: 1 }), {
      initialProps: { value: 0 },
    });
    expect(result.current).toBe(0);

    rerender({ value: 10 });
    raf.flush(0); // first frame establishes the start time, elapsed = 0
    expect(result.current).toBe(0);

    raf.flush(300); // halfway through the duration
    expect(result.current).toBeGreaterThan(0);
    expect(result.current).toBeLessThan(10);

    raf.flush(600); // duration elapsed -- lands exactly on target
    expect(result.current).toBe(10);
  });

  it("continues from where it was when the value changes mid-tween, never falling back", () => {
    // A refetch or an agency switch can land inside the entrance window that
    // now runs on every first paint. The next tween has to start from the
    // figure on screen; starting from the interrupted tween's own origin
    // would walk the number backwards, usually to 0.
    const raf = mockRaf();
    const { result, rerender } = renderHook(({ value }) => useCountUp(value, { duration: 600, decimals: 1 }), {
      initialProps: { value: 10 },
    });

    raf.flush(0);
    raf.flush(300);
    const midTween = result.current;
    expect(midTween).toBeGreaterThan(0);
    expect(midTween).toBeLessThan(10);

    rerender({ value: 20 });
    raf.flush(1000); // first frame of the new tween: elapsed 0, so it shows its start
    expect(result.current).toBe(midTween);

    raf.flush(1600);
    expect(result.current).toBe(20);
  });

  it("jumps straight to the target under prefers-reduced-motion: reduce", () => {
    setReducedMotion(true);
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result, rerender } = renderHook(({ value }) => useCountUp(value), {
      initialProps: { value: 0 },
    });
    rerender({ value: 25 });
    expect(result.current).toBe(25);
    expect(raf).not.toHaveBeenCalled();
  });

  it("jumps straight to the target when duration is 0", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result, rerender } = renderHook(({ value }) => useCountUp(value, { duration: 0 }), {
      initialProps: { value: 0 },
    });
    rerender({ value: 7 });
    expect(result.current).toBe(7);
    expect(raf).not.toHaveBeenCalled();
  });

  it("rounds the intermediate value to the requested decimals", () => {
    const raf = mockRaf();
    const { result, rerender } = renderHook(({ value }) => useCountUp(value, { duration: 100, decimals: 0 }), {
      initialProps: { value: 0 },
    });
    rerender({ value: 9 });
    raf.flush(0);
    raf.flush(50);
    expect(Number.isInteger(result.current)).toBe(true);
  });
});
