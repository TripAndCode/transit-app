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

  it("does not animate on first mount -- returns the initial value immediately", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result } = renderHook(() => useCountUp(42));
    expect(result.current).toBe(42);
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
